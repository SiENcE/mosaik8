# The source of this sample

This sample has no generator script. The committed art and data ARE its
source: `valley_src.png` (the full-colour original), the 16-colour indexed
tileset `../tiles.png` (its PLTE is the palette) and `../world.toml`. They are
not regenerated from a public script; edit them directly and re-run the scene
transpiler:

    python -m mosaik_scenes projects/pal-lab/world.toml -o projects/pal-lab/src/scenes.mos
