# The source of this sample

This sample has no generator script. The committed art and data ARE its
source: `tiles.png` and `world.toml` here, plus the event scripts and HUD
panels under `../scripts/`. They are not regenerated from a public script;
edit them directly and re-run the transpilers:

    python -m mosaik_scenes projects/vm-hud/assets/world.toml -o projects/vm-hud/src/scenes.mos
    python -m mosaik_vm projects/vm-hud/scripts -o projects/vm-hud/src/scripts.mos
