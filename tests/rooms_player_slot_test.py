"""THE PLAYER'S FAN HAS ONE BASE, and the generated room code uses it
everywhere (2026-10-18).

`player.set_base(...)` puts the player's metasprite at OAM 0, or at
EMOTE_FAN when an emote pack owns objects 0..3 (an OAM index is the draw
priority on the GB family). Every other line of the generated rooms.mos that
addresses the player's fan by a FIXED slot must name that same slot. One did
not: the room load coloured the player at a literal 0 while it lived at
EMOTE_FAN, so the reference-engine sample conversion's platform player lost
its per-cell palettes (they went onto the hidden emote objects); the
engine's own vm-shardlings had the same line (record in the studio's
`docs/done/list-object-flips-and-shmup-facing.md`).

`player_paint_base_test.py` pins that one line. This test pins the RULE, so
a fixed-slot call added later is caught too: rooms.mos is regenerated for a
temporary copy of every VM sample in projects/, plus vm-shardlings variants
that switch the emote pack and both colour arms on and off, and in each, any
`sprite.<verb>(<slot>, ...)` or `scenes.paint_actor(<slot>, ...)` with a
FIXED first argument (a number, EMOTE_FAN or EMOTE_OAM) must use the slot
`player.set_base` was given (0 when the file never calls it). Qt-free, no
toolchain.
"""
import io
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

FAILS = []
FIXED_CALL = re.compile(
    r"\b(sprite\.[a-z_]+|scenes\.paint_actor)\(\s*(EMOTE_FAN|EMOTE_OAM|\d+)\s*,")
SET_BASE = re.compile(r"\bplayer\.set_base\(\s*([A-Za-z_]\w*|\d+)\s*\)")


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _strip_comments(text):
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def violations(rooms_text):
    """[(line, slot)] fixed-slot calls that miss the player's base, and the
    base itself."""
    code = _strip_comments(rooms_text)
    bases = set(SET_BASE.findall(code))
    base = bases.pop() if len(bases) == 1 else ("0" if not bases else None)
    bad = []
    if base is None:
        return [("player.set_base takes more than one slot", "?")], None
    for m in FIXED_CALL.finditer(code):
        if m.group(2) != base:
            line = code[:m.start()].count("\n") + 1
            bad.append(("%d: %s" % (line, code.splitlines()[line - 1].strip()), m.group(2)))
    return bad, base


def _vm_projects():
    pdir = os.path.join(ROOT, "projects")
    for name in sorted(os.listdir(pdir)):
        p = os.path.join(pdir, name)
        if os.path.exists(os.path.join(p, "src", "rooms.mos")):
            yield name, p


def _regen(src, tmp, name, edit=None):
    dst = os.path.join(tmp, name)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("build"))
    if edit:
        edit(dst)
    import mosaik_vm
    mosaik_vm.generate_rooms(dst)
    return io.open(os.path.join(dst, "src", "rooms.mos"), encoding="utf-8").read()


def _world(dst):
    for rel in ("world.toml", os.path.join("assets", "world.toml")):
        if os.path.exists(os.path.join(dst, rel)):
            return os.path.join(dst, rel)
    raise AssertionError("no world.toml in %s" % dst)


def _set_player_kind_pal(slot):
    def edit(dst):
        wp = _world(dst)
        text = io.open(wp, encoding="utf-8").read()
        text, n = re.subn(r"(?m)^(\[kind_palettes\]\n(?:[^\[\n].*\n)*?)player = \d+$",
                          lambda m: m.group(1) + "player = %d" % slot, text, count=1)
        assert n == 1, "vm-shardlings' [kind_palettes] player row moved"
        io.open(wp, "w", encoding="utf-8").write(text)
    return edit


def _add_cell_map(dst):
    _set_player_kind_pal(3)(dst)
    wp = _world(dst)
    with io.open(wp, "a", encoding="utf-8") as f:
        f.write("\n[kind_tile_palettes]\nplayer = [3, 3, 3, 3, 3, 3, 3, 3]\n")


def _drop_emotes(dst):
    """No emote pack: strip every actor_emote event from the scripts."""
    sd = os.path.join(dst, "scripts")
    for fn in os.listdir(sd):
        if not fn.endswith(".evt.toml"):
            continue
        p = os.path.join(sd, fn)
        text = io.open(p, encoding="utf-8").read()
        text = re.sub(r'(?m)^event = "actor_emote"$', 'event = "wait"\nframes = 1', text)
        io.open(p, "w", encoding="utf-8").write(text)


def main():
    import mosaik_vm  # noqa: F401  (fail loudly here if the toolchain moved)
    tmp = tempfile.mkdtemp(prefix="rooms_slot_")
    checked_calls = 0
    emote_colour_seen = False
    try:
        print("\n[every VM sample, regenerated]")
        for name, path in _vm_projects():
            try:
                text = _regen(path, tmp, name)
            except Exception as e:      # noqa: BLE001
                check("%s regenerates" % name, False, e)
                continue
            bad, base = violations(text)
            checked_calls += len(FIXED_CALL.findall(_strip_comments(text)))
            check("%s: fixed-slot calls use the player's base (%s)" % (name, base),
                  not bad, bad)

        print("\n[vm-shardlings variants: emote pack x colour arm]")
        shard = os.path.join(ROOT, "projects", "vm-shardlings")
        variants = (
            ("emote + kind palette", _set_player_kind_pal(3)),
            ("emote + cell map", _add_cell_map),
            ("no emote + kind palette",
             lambda d: (_set_player_kind_pal(3)(d), _drop_emotes(d))),
            ("no emote + cell map", lambda d: (_add_cell_map(d), _drop_emotes(d))),
        )
        for i, (label, edit) in enumerate(variants):
            text = _regen(shard, tmp, "shard%d" % i, edit)
            bad, base = violations(text)
            code = _strip_comments(text)
            colour = re.search(r"\b(sprite\.set_palette|scenes\.paint_actor)\(\s*"
                               r"(EMOTE_FAN|\d+)\s*,", code)
            check("%s: the player is coloured at a fixed slot" % label, colour is not None)
            check("%s: fixed-slot calls use the player's base (%s)" % (label, base),
                  not bad, bad)
            if base == "EMOTE_FAN" and colour:
                emote_colour_seen = True
            if label.startswith("no emote"):
                check("%s: without the pack the base is 0" % label, base == "0", base)
            checked_calls += len(FIXED_CALL.findall(code))
        check("the rule had something to check (%d fixed-slot calls)" % checked_calls,
              checked_calls >= 4)
        check("an emote pack AND a player colour were generated together",
              emote_colour_seen)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("rooms.mos: one base for the player's fan")
    print("=" * 60)
    main()
    print()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("rooms_player_slot_test: all checks passed")
