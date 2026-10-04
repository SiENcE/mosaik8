"""vm-cam runs its own cutscene: wait, lock, pan to (64, 0), shake 40 @ 4,
release. Its pinned camera y is 0 - exactly where a CLAMPED shake and a
WRAPPED one differ, so this is the A/B the 6.4 plan asks for ("vm-cam must
stay visually identical"). Reports the SCY values the shake actually reached,
sampled over the shake window only (the release afterwards moves the camera
to the follow position and would swamp it)."""
import sys
from pyboy import PyBoy

pb = PyBoy(sys.argv[1], window="null", sound_emulated=False)
scy, scx = [], []
for i in range(170):
    pb.tick()
    if 118 <= i <= 158:                      # the shake, between pan and release
        p = pb.screen.tilemap_position_list
        scy += [e[1] for e in p]
        scx += [e[0] for e in p]
print("  SCX during the shake: %s" % sorted(set(scx)))
print("  SCY during the shake: %s" % sorted(set(scy)))
pb.stop(save=False)
