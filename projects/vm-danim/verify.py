"""PyBoy proof of DATA-DRIVEN VM8 animation (Option X): a SCRIPT event
(actor_set_clip) assigns the clip, and the runtime (vm.canim, via core.set_anim)
animates the actor -- no shell animation code. Confirms the actor animates (tile
cycles), paces (OAM x sweeps), and mirrors via flip_left (OAM FLIP_X bit toggles)."""
import os

ROM = os.path.join(os.path.dirname(__file__), "build", "gameboy", "vm-danim.gb")
from pyboy import PyBoy   # noqa: E402

S_FLIPX = 0x20


def oam(pb, slot):
    b = 0xFE00 + slot * 4
    return pb.memory[b], pb.memory[b + 1], pb.memory[b + 2], pb.memory[b + 3]


def main():
    pb = PyBoy(ROM, window="null")
    for _ in range(200):
        pb.tick()
    ey, ex, etile, _ = oam(pb, 0)      # actor 0 base = OAM slot 0
    print("[boot] actor@0 x=%d y=%d tile=%d" % (ex, ey, etile))
    assert 0 < ey < 160, "actor not spawned"

    tiles, xs, flips = set(), set(), set()
    for _ in range(400):
        pb.tick()
        _, ex, etile, eattr = oam(pb, 0)
        tiles.add(etile)
        xs.add(ex)
        flips.add((eattr & S_FLIPX) != 0)
    print("[run] tiles=%s  x span=%d..%d  FLIP_X seen=%s"
          % (sorted(tiles), min(xs), max(xs), sorted(flips)))
    assert len(tiles) >= 2, "actor did not animate (tile did not cycle)"
    assert max(xs) - min(xs) > 30, "actor did not pace (script move)"
    assert True in flips and False in flips, "flip_left (FLIP_X) never toggled"

    pb.stop()
    print("PASS")


if __name__ == "__main__":
    main()
