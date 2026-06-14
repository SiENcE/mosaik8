# vendor-override — using `lib/game/` while overriding one module

A minimal top-down walker that shows the two ways a game pulls in the
[game framework](../../docs/game-framework.md), **at the same time**:

- most framework modules come from the shared **`lib/` search path** (no copies), and
- **one** module is **vendored and modified** locally, overriding the shared copy
  for this project only.

## How import resolution works

When the build follows an `import "game.camera"`, it looks for the module in this
order:

1. **Relative to the importing file first** — here, `src/game/camera.mos`.
2. then the **library search roots**: a project's `[lib] paths` (in
   `mosaik.toml`), the `MOSAIK_LIB` env var, and finally the default `lib/` next
   to the build tool.

The **first match wins**. So:

| Import in `src/main.mos` | Resolves to | Why |
|---|---|---|
| `game.camera` | `src/game/camera.mos` | a local copy exists → it wins |
| `game.pad` | `lib/game/pad.mos` | no local copy → falls through to `lib/` |
| `game.collision` | `lib/game/collision.mos` | "" |
| `game.topdown` | `lib/game/topdown.mos` | "" |

You can see this in the build output:

```
$ python mosaik8.py build projects/vendor-override
  Found 2 source file(s):
    • .../src/main.mos
    • .../src/game/camera.mos      <- the local override
  Library modules (3):
    + .../lib/game/pad.mos         <- pulled from lib/, no copy
    + .../lib/game/collision.mos
    + .../lib/game/topdown.mos
```

`game.camera` is **not** listed under "Library modules" — it was satisfied by the
local copy. Only the modules an import actually reaches are pulled in (whole-
program tree-shaking trims the rest), so adding `lib/` to the search path costs
nothing for modules you don't use.

## The modification

`src/game/camera.mos` is a copy of [`lib/game/camera.mos`](../../lib/game/camera.mos)
with one behavioural change: this game is a horizontally-scrolling corridor, so
`follow()` pins the vertical axis (`camy = 0`) and only scrolls on X. The stock
`lib` camera follows **both** axes. Everything else — the exported names, the
signatures, the `axis()` clamp — is identical, so `main.mos` composes it exactly
as it would the stock module; only the runtime behaviour differs.

Delete `src/game/camera.mos` and rebuild, and the import falls through to
`lib/game/camera.mos` — the camera follows both axes again. The override is just
a file in the right place; nothing in `main.mos` or `mosaik.toml` changes.

## When to vendor vs. use the lib path

- **Use the lib path (no copy)** when you want the framework module as-is. This
  is the default for the other sample projects (`box-pusher`, `scene-demo`,
  `platformer`) — none of them carry a `src/game/` folder anymore.
- **Vendor a copy** only when you need a project-local *modification* (a tweaked
  clamp, a forked genre kit, an experiment). The copy overrides `lib/` for that
  project without affecting any other project or the shared modules.

## Caveat (build-tool gotcha)

The import scanner is a simple text match, so **avoid writing a quoted
`import "…"` inside a comment** — it would be picked up as a real import. (Refer
to a module in prose without the quotes, e.g. "a game.camera import".)

## Build

```
python mosaik8.py build projects/vendor-override                  # all target_platforms
python mosaik8.py build --platform lynx projects/vendor-override  # one console
```

Builds on all nine consoles. Walk with the D-pad: the player moves freely in the
room, but the camera only scrolls left/right (the vendored override) instead of
following vertically.
