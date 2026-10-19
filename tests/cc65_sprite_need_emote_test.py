#!/usr/bin/env python3
"""The cc65 sprite-slot need must count the emote bubble's reserved objects.

A residency world that targets the PC Engine or the Lynx states its busiest
room's need (rooms `SPR_SLOT_NEED`), and the build sizes the PCE's sprite
pool to it. `load_room` places actors from `EMOTE_FAN` + the player's fan
when the world uses emotes, but the need counted the player's fan and the
placed fans only: four slots short, so the last actor's fan landed past the
pool's end and was dropped (projects/vm-shardlings' PCE battle drew no back
view, 2026-10-19: need 56, placement end 60).

The check generates rooms.mos for a copy of projects/vm-shardlings (emotes,
a 16x16 player, a battle room with a foe and an ally placeholder) and compares
SPR_SLOT_NEED with the battle room's placement end computed from the
project's own sprite manifests (no emote-less control: the sample's scripts
name its emote, so a copy without the sheets does not compile).
"""
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []
EMOTE_FAN = 4


def check(cond, msg):
    print("  [%s] %s" % ("ok" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def _fan(root, stem):
    """8x8 tiles of a sheet's first frame (one PCE sprite each)."""
    text = open(os.path.join(root, "assets", stem + ".sprites.toml"), encoding="utf-8").read()
    w, h = map(int, re.search(r"rect = \[\s*\d+,\s*\d+,\s*(\d+),\s*(\d+)\s*\]", text).groups())
    return (w // 8) * (h // 8)


def _need():
    import mosaik_vm
    import tomllib
    src = os.path.join(ROOT, "projects", "vm-shardlings")
    with tempfile.TemporaryDirectory() as tmp:
        dst = os.path.join(tmp, "vm-shardlings")
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("build", "__pycache__"))
        mosaik_vm.generate_rooms(dst)
        rooms = open(os.path.join(dst, "src", "rooms.mos"), encoding="utf-8").read()
        m = re.search(r"const SPR_SLOT_NEED: u8 = (\d+)", rooms)
        with open(os.path.join(dst, "world.toml"), "rb") as f:
            world = tomllib.load(f)
        studio = open(os.path.join(dst, "studio.toml"), "rb")
        sprites = tomllib.load(studio)["kind_sprites"]
        studio.close()
        variants = world["kind_variants"]
        battle = next(s for s in world["scene"] if s["name"] == "battle")
        end = _fan(dst, sprites["player"])
        for obj in battle["object"]:
            kinds = variants.get(obj["kind"], {}).get("kinds") or [obj["kind"]]
            end += max(_fan(dst, sprites[k]) for k in kinds)
        return (int(m.group(1)) if m else None), end, "EMOTE_FAN" in rooms


def test_need_counts_the_emote_reservation():
    need, end, emote = _need()
    check(emote, "the sample reserves the emote bubble's objects (EMOTE_FAN)")
    check(need is not None, "rooms.mos states SPR_SLOT_NEED (the sample targets the PCE)")
    check(need == end + EMOTE_FAN,
          "SPR_SLOT_NEED %s is the battle room's placement end: EMOTE_FAN + %d" % (need, end))


def main():
    print("cc65_sprite_need_emote_test")
    test_need_counts_the_emote_reservation()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
