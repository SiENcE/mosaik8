#!/usr/bin/env python3
"""The 2026-11-03 engine review's GROUP 1: reachable bugs, one check each.

Every item here was reachable from converted or first-party content and is
fixed with the smallest change that keeps a program that does not use the
feature byte-identical (each fix is folded on the opcode that reaches it,
where one exists). The ids are the review's own.

  H2   engine.scroll refill_rows: the SMS ring window is [s_base+1, s_base+32]
       for the row band too (refill / refill_cols already shifted; the band
       repainted every SMS box close in a wide room into the wrong column).
  H3   rooms generator: a menu inside a `switch` case (`cases[].then`) or a
       `while` body still wires core.set_choice; the other feature scans
       look inside a `while` body too.
  #30  vm.music play(): starting a song while one plays silences the old
       song's voices first (GB / Lynx / SMS-GG; the PCE already did), and the
       hUGE path cuts the voices on a track change and clears the game mute
       BEFORE its same-song early-out (the reference VM's vm_music_play order).
  M7   a player-hit cause with no registered script is dropped, not handed
       to a later scene's first shot.
  M8   INPUT_ATTACH into a FREED slot clears the busy gate the old button's
       script left there.
  M9   SMS/GG: a dismiss during the staged box OPEN waits for the text.
  M10  move options die with the move (arrival, cancel, room reset), so a
       later plain move is plain; refvm mirrors it.
  M11  knockback does not survive a room load.
  M12  knockback owns the pad: no jump, no ladder grab, no held-jump sustain.
  H1   vm.entity kills an On Update by GENERATION, never by a bare handle.

Most checks read the engine source (sliced to the one FUNCTION, never a
whole-file substring); H2 and the hUGE half compile the real module and read
the generated C; H3 runs the generator on a temp world; M10's refvm half runs
the reference VM.
"""

import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def block_at(src, i):
    """The text of the brace block whose `{` is the first one at/after i."""
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j + 1:k]
    raise ValueError("unbalanced block")


def mos_fns(src, name):
    """Every body of `function name(` in a .mos file, in file order (a module
    with per-console forks defines one function several times)."""
    pat = re.compile(r"(?:hot )?(?:local )?function %s\(" % re.escape(name))
    out = []
    for m in pat.finditer(src):
        # a comment line that names the function is not a definition
        line_start = src.rfind("\n", 0, m.start()) + 1
        if "--" in src[line_start:m.start()]:
            continue
        out.append(block_at(src, m.end()))
    return out


def strip_comments(body):
    return "\n".join(ln.split("--", 1)[0] for ln in body.splitlines())


def c_body(c, name):
    m = re.search(r"^[\w ]*\b%s\([^;{]*\)\s*(?:BANKED\s*|NONBANKED\s*)?\{\n(.*?)\n\}"
                  % re.escape(name), c, re.S | re.M)
    return m.group(1) if m else ""


# --------------------------------------------------------------------- H2

SCROLL_PROBE = '''
module "main" {
    import "engine.scroll"
    import "platform.video"

    function gather(c: u16) {
        var t: u8 = c
        scroll.buf[0] = t
    }

    function main() {
        video.enable_lcd()
        scroll.fill(64, 18, gather)
        loop {
            scroll.update(0, gather)
            scroll.refill_rows(2, 3, gather)
            video.wait_vblank()
        }
    }
    export main
}
'''


def compile_with_lib(lib_rel, probe, platform):
    from mosaik import MosaikCompiler
    srcs = [(os.path.basename(lib_rel), read(lib_rel)), ("main.mos", probe.strip())]
    return MosaikCompiler().compile_program(srcs, platform=platform)


def test_h2():
    print("H2 engine.scroll refill_rows uses the SMS ring window")
    sms = compile_with_lib("lib/engine/scroll.mos", SCROLL_PROBE, "sms")
    gb = compile_with_lib("lib/engine/scroll.mos", SCROLL_PROBE, "gameboy")
    gg = compile_with_lib("lib/engine/scroll.mos", SCROLL_PROBE, "gamegear")
    rs, rg, rgg = (c_body(x, "engine_scroll_refill_rows") for x in (sms, gb, gg))
    check(bool(rs) and bool(rg) and bool(rgg), "every target emits refill_rows")
    check(re.search(r"lc = \(lc \+ 1\)", rs) is not None,
          "sms: the band column is s_base + c + 1")
    check(re.search(r"lc = \(lc \+ 1\)", rg) is None,
          "gameboy: no shift (byte-identical)")
    check(re.search(r"lc = \(lc \+ 1\)", rgg) is None,
          "gamegear: no shift (its viewport hides the seam)")


# --------------------------------------------------------------------- H3

def _world(root, events):
    import toml
    import mosaik_assets
    os.makedirs(os.path.join(root, "assets"))
    os.makedirs(os.path.join(root, "src"))
    os.makedirs(os.path.join(root, "scripts"))
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    mosaik_assets.write_png_indexed(
        os.path.join(root, "assets", "t.png"), 8, 16, [0] * 64 + [3] * 64, pal)
    world = {
        "world": {"module": "scenes", "map_w": 20, "map_h": 18, "vm": True},
        "tileset": {"png": "t.png"},
        "kinds": {"player": 0},
        "scene": [{"name": "room", "scene_type": "topdown",
                   "map": [[0] * 20 for _ in range(18)],
                   "on_init": "main",
                   "object": [{"kind": "player", "x": 32, "y": 32}]}],
    }
    with open(os.path.join(root, "assets", "world.toml"), "w",
              encoding="utf-8") as f:
        toml.dump(world, f)
    with open(os.path.join(root, "scripts", "main.evt.toml"), "w",
              encoding="utf-8") as f:
        toml.dump({"script": [{"name": "main", "events": events}]}, f)
    open(os.path.join(root, "src", "rooms.mos"), "w").close()
    return root


def _rooms(tmp, name, events):
    import mosaik_vm
    root = _world(os.path.join(tmp, name), events)
    path = mosaik_vm.generate_rooms(root)
    with open(path, encoding="utf-8") as f:
        return f.read()


MENU = {"event": "menu", "var": "pick", "options": ["YES", "NO"]}
SHOT = {"event": "projectile", "kind": "player", "vx": 1, "vy": 0}


def test_h3():
    print("H3 the feature scans look inside switch cases and while bodies")
    tmp = tempfile.mkdtemp(prefix="review_h3_")
    try:
        top = _rooms(tmp, "top", [MENU])
        in_case = _rooms(tmp, "case", [
            {"event": "switch", "value": "1",
             "cases": [{"value": 1, "then": [MENU]}]}])
        in_while = _rooms(tmp, "loop", [
            {"event": "while", "cond": "1", "then": [MENU]}])
        none = _rooms(tmp, "none", [{"event": "wait", "frames": 1}])
        shot_loop = _rooms(tmp, "shot", [
            {"event": "while", "cond": "1",
             "then": [SHOT, {"event": "wait", "frames": 1}]}])
        shot_none = _rooms(tmp, "noshot", [
            {"event": "while", "cond": "1",
             "then": [{"event": "wait", "frames": 1}]}])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check("core.set_choice(" in top, "control: a top-level menu wires set_choice")
    check("core.set_choice(" not in none, "control: no menu, no set_choice")
    check("core.set_choice(" in in_case,
          "a menu only inside a switch case wires set_choice")
    check("core.set_choice(" in in_while,
          "a menu only inside a while body wires set_choice")
    check("core.set_projectiles(" not in shot_none,
          "control: no projectile, no projectile pool")
    check("core.set_projectiles(" in shot_loop,
          "a projectile only inside a while body wires the projectile pool")


# --------------------------------------------------------------------- #30

def test_music_play_silences():
    print("#30 vm.music play() silences the previous song first")
    src = read("lib/vm/music.mos")
    plays = mos_fns(src, "play")
    check(len(plays) >= 4, "music.mos defines play() per console (%d)" % len(plays))
    for label, body in zip(("gameboy", "lynx", "sms/gg"), plays[:3]):
        b = strip_comments(body)
        i_stop = b.find("stop()")
        i_song = b.find("m_song = song")
        guard = re.search(r"if m_playing == 1 \{\s*stop\(\)", b)
        check(guard is not None and 0 <= i_stop < i_song,
              "%s: play() calls stop() while a song plays, before the new "
              "song's state (m_sfx_reserve is still the OLD song's)" % label)
    # the hUGE path, read off the generated C
    probe = '''
module "main" {
    import "native.huge"
    function main() {
        huge.play(1)
        loop { huge.update() }
    }
    export main
}
'''
    from mosaik import MosaikCompiler
    c = MosaikCompiler().compile_program([("main.mos", probe.strip())],
                                         platform="gameboy")
    body = c_body(c, "gbs_huge_play")
    check(bool(body), "native.huge emits gbs_huge_play")
    i_mute = body.find("gbs_huge_gmute = 0x00")
    i_ret = body.find("return;")
    check(0 <= i_mute < i_ret,
          "hUGE: the game mute is cleared BEFORE the same-song early-out")
    check("NR12_REG" not in body,
          "hUGE: the resident prelude pays nothing for the cut")
    # the cut is in the generated song table, and only with a SECOND song
    import copy
    import toml
    from mosaik_vm import huge
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import hugedriver_test as hd
    two = copy.deepcopy(hd.SONGS)
    two["song"]["Two"] = copy.deepcopy(two["song"]["One"])
    tables = {}
    for label, songs in (("one", hd.SONGS), ("two", two)):
        tmp = tempfile.mkdtemp(prefix="review_huge_")
        try:
            os.makedirs(os.path.join(tmp, "scripts"))
            for fn, data in (("songs.toml", songs), ("instruments.toml", hd.INSTS)):
                with open(os.path.join(tmp, "scripts", fn), "w",
                          encoding="utf-8") as f:
                    toml.dump(data, f)
            huge.generate_huge_songs(tmp, os.path.join(tmp, "out"))
            with open(os.path.join(tmp, "out", "songs_huge.c"),
                      encoding="utf-8") as f:
                tables[label] = c_body(f.read(), "gbs_huge_song_init")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    cut = "NR12_REG = NR22_REG = NR32_REG = NR42_REG = 0"
    check(bool(tables["one"]) and cut not in tables["one"],
          "hUGE: a ONE-song project emits no cut (it cannot switch tracks)")
    t2 = tables["two"]
    check(0 <= t2.find(cut) < t2.find("hUGE_init("),
          "hUGE: with a second song, a track change cuts the old voices "
          "before hUGE_init")


# ------------------------------------------------------------------ core

def test_core():
    src = read("lib/vm/core.mos")
    print("M7 a player-hit cause with no script is dropped")
    fph = strip_comments(mos_fns(src, "fire_player_hit")[0])
    check(re.search(r"if VM_OP_SET_PLAYER_HIT \{\s*if has_player_hit == 0 \{"
                    r"\s*ph_cause = 0", fph) is not None,
          "fire_player_hit clears ph_cause when no script is registered")

    print("M8 INPUT_ATTACH into a freed slot drops the old busy gate")
    i = src.index("local function input_attach(")
    arm = strip_comments(block_at(src, i))
    free = arm.find("in_active[i] == 0")
    clr = arm.find("in_ctx[i] = 255")
    fill = arm.find("in_active[slot] = 1")
    same = arm.find("in_btn[i] == btn")
    check(0 <= same < free, "the same-button re-attach is scanned first")
    check(0 <= free < clr < fill,
          "the gate is cleared on the FREE-slot path only (a re-attach of "
          "the same button keeps it, the reference's rule)")

    print("M9 SMS/GG: no dismiss while the staged open is still drawing")
    ri = strip_comments(mos_fns(src, "read_input")[0])
    j = ri.find("if box_open == 1 {")
    seg = ri[j:j + 400]
    check(re.search(r'if platform == "sms" or platform == "gamegear" \{\s*'
                    r'if ui_stage != 0 \{', seg) is not None,
          "read_input waits for ui_stage inside the open-box branch")


# ----------------------------------------------------------------- actor

def test_actor():
    src = read("lib/vm/actor.mos")
    print("M10 move options die with the move")
    sm = strip_comments(mos_fns(src, "set_moving")[0])
    check(re.search(r"if VM_OP_A_MOVE_OPTS \{\s*a_mopt\[i\] = 0", sm) is not None,
          "set_moving(i, 0) releases a_mopt (arrival, cancel, deactivate)")
    rs = strip_comments(mos_fns(src, "reset")[0])
    check(re.search(r"if VM_OP_A_MOVE_OPTS \{[^}]*pend_mopt = 0", rs, re.S)
          is not None and "a_mopt[i] = 0" in rs,
          "reset() drops the latch and every slot's options")

    print("M10 refvm: a plain move after an opted one is plain")
    from mosaik_vm import isa
    from mosaik_vm.refvm import RefVM
    vm = RefVM(bytes([isa.OPS["STOP"][0]]))
    vm.pend_mopt = 2                    # vertical first, no collision
    vm._move_start(0, 4, 4)             # what A_MOVE_START runs
    for _ in range(64):
        if not vm.actors[0].moving:
            break
        vm.frame()
    check(not vm.actors[0].moving, "refvm: the non-blocking move arrives")
    check(vm.actors[0].mopt == 0,
          "refvm: the options are released on arrival (a later plain "
          "A_MOVE_TO reads 0)")


# ---------------------------------------------------------------- player

def test_player():
    src = read("lib/vm/player.mos")
    print("M11 knockback does not survive a room load")
    cw = strip_comments(mos_fns(src, "clear_wide")[0])
    check(re.search(r"if VM_OP_PLAYER_KNOCKBACK \{\s*kb_frames = 0", cw)
          is not None, "clear_wide zeroes kb_frames (folded)")

    print("M12 knockback owns the pad")
    jg = strip_comments(mos_fns(src, "jump_gated")[0])
    check(re.search(r"if VM_OP_PLAYER_KNOCKBACK \{\s*if kb_frames > 0 \{\s*"
                    r"return true", jg) is not None,
          "jump_gated refuses every jump while knocked back")
    kb = strip_comments(mos_fns(src, "knockback")[0])
    check(re.search(r"if VM_OP_PLAYER_KNOCKBACK \{[^}]*pj_up = 0", kb, re.S)
          is not None, "knockback() ends a held-jump sustain")
    check(re.search(r"if VM_OP_PLAYER_KNOCKBACK \{\s*if kb_frames == 0 \{\s*"
                    r"lad_join\(\)", strip_comments(src)) is not None,
          "a ladder grab waits for the knockback to end")


# ---------------------------------------------------------------- entity

def test_entity():
    src = read("lib/vm/entity.mos")
    print("H1 vm.entity kills an On Update by generation")
    for name in ("reset", "stop_update"):
        body = strip_comments(mos_fns(src, name)[0])
        check("core.kill_gen(en_uthread[i], en_ugen[i])" in body
              and "core.kill(" not in body,
              "%s() kills through core.kill_gen" % name)
    adds = [strip_comments(b) for b in mos_fns(src, "add")]
    check(len(adds) == 2 and all("en_ugen[slot] = core.gen_of(uh)" in a
                                 or "en_ugen[slot] = 0" in a for a in adds),
          "both add() forks record the On Update's generation")


def main():
    test_h2()
    test_h3()
    test_music_play_silences()
    test_core()
    test_actor()
    test_player()
    test_entity()
    print("=" * 50)
    if _FAILED:
        print("Some tests failed:")
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
