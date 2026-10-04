"""PyBoy proof of the VM8 animation system (vm.clip + mosaik_anim) on vm-clipdemo.

Confirms: the background renders; the PLAYER metasprite animates (its base tile
cycles while walking); the ENEMY actor animates (tile cycles), MOVES (its OAM x
sweeps), and MIRRORS via flip_left (its OAM FLIP_X attribute bit toggles as it paces
left vs right).
"""
import os
import re

HERE = os.path.dirname(__file__)
ROM = os.path.join(HERE, "build", "gameboy", "vm-clipdemo.gb")
from pyboy import PyBoy   # noqa: E402

S_FLIPX = 0x20


def _player_slot():
    """The player base OAM slot = `actor.slots()` = actor_pool * 2 * 2.

    DERIVED, never hardcoded: main.mos passes `actor.slots()` to
    `player.set_base`, so the slot moves with `[build] actor_pool` (the sample
    sets 2; the runtime default is 8). Pinning the old 32 made every player
    check read an empty OAM entry the moment the pool was tuned."""
    toml = os.path.join(HERE, "mosaik.toml")
    pool = 8
    with open(toml, encoding="utf-8") as f:
        m = re.search(r"^\s*actor_pool\s*=\s*(\d+)", f.read(), re.M)
        if m:
            pool = int(m.group(1))
    return pool * 2 * 2


PLAYER = _player_slot()


def oam(pb, slot):
    b = 0xFE00 + slot * 4
    return pb.memory[b], pb.memory[b + 1], pb.memory[b + 2], pb.memory[b + 3]  # y,x,tile,attr


def bg_tiles(pb):
    return len(set(pb.memory[0x9800 + i] for i in range(0x400)))


def main():
    pb = PyBoy(ROM, window="null")
    for _ in range(200):
        pb.tick()

    tiles = bg_tiles(pb)
    py, px, ptile, _ = oam(pb, PLAYER)     # player base = OAM slot actor.slots()
    ey, ex, etile, eattr = oam(pb, 0)      # enemy base = OAM slot 0
    print("[boot] bg tiles=%d  player@%d (x=%d tile=%d)  enemy@0 (x=%d tile=%d)"
          % (tiles, PLAYER, px, ptile, ex, etile))
    assert tiles >= 2, "background did not render (only the sky tile)"
    assert 0 < py < 160, "player metasprite missing"
    assert 0 < ey < 160, "enemy actor missing"

    # PLAYER animates while walking: its base tile cycles.
    ptiles = set()
    pb.button_press("right")
    for _ in range(60):
        pb.tick()
        ptiles.add(oam(pb, PLAYER)[2])
    pb.button_release("right")
    print("[player] walk base tiles=%s" % sorted(ptiles))
    assert len(ptiles) >= 2, "player did not animate (walk cycle)"

    # ENEMY animates + moves + flips: watch a full pace cycle.
    etiles = set()
    exs = set()
    flips = set()
    for _ in range(600):
        pb.tick()
        _, ex, etile, eattr = oam(pb, 0)
        etiles.add(etile)
        exs.add(ex)
        flips.add((eattr & S_FLIPX) != 0)
    print("[enemy] tiles=%s  x span=%d..%d  FLIP_X seen=%s"
          % (sorted(etiles), min(exs), max(exs), sorted(flips)))
    assert len(etiles) >= 2, "enemy did not animate (walk cycle)"
    assert max(exs) - min(exs) > 30, "enemy did not pace across the screen"
    assert True in flips and False in flips, "enemy flip_left (FLIP_X) never toggled"

    pb.stop()
    print("PASS")


if __name__ == "__main__":
    main()
