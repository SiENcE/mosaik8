# vm-danim

The **data-driven** VM8 animation proof (Option X) -- the studio-VM model, where
logic (including animation) is DATA, not shell code.

A creature is spawned, assigned a clip, and paced ENTIRELY from a SCRIPT
(`scripts/main.evt.toml`): `actor_activate` -> **`actor_set_clip`** -> `actor_move`.
The near-fixed shell only registers the world's clips (`vm.canim.set_clips`) and
hands the driver to the loop (`core.set_anim(vm.canim.tick_all)`); the runtime then
animates the actor automatically -- its **state** from movement (idle vs walk), its
**facing** from the move direction, its LEFT facing mirrored from RIGHT via
`flip_left` (FLIP_X). No per-actor animation code anywhere.

Contrast `vm-clipdemo`, which drives the same clips from SHELL code (`vm.clip`). This
one proves the path a studio VM project uses: clips authored in the Animation dock
(`studio.toml [animations.<kind>]`) -> `mosaik_vm.generate_clips` -> `src/clips.mos`
-> a script event turns it on.

## Build / verify

```
python projects/vm-danim/assets/gen.py
python mosaik_vm.py projects/vm-danim/scripts -o projects/vm-danim/src/scripts.mos
python mosaik8.py build --platform gameboy projects/vm-danim
python projects/vm-danim/verify.py
```

Targets `gameboy` / `gameboy_color` (FLIP_X is a no-op on SMS/GG). PyBoy-verified: the
actor's tile cycles (walk), its OAM x sweeps (the scripted pace), and its FLIP_X bit
toggles as it turns -- all from the script + the runtime.
