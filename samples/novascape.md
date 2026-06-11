# Novascape — mosaik port (work in progress)

A port of the 4-ROM-bank MBC1 game in `docs/novascape/` to mosaik.

mosaik has no ROM-bank switching and no cross-translation-unit linking, so the
port is **flattened into a single module** and the original bank switches become
a **game-state machine**. The large tile/map tables are extracted verbatim from
the original C sources by a generator (no hand transcription).

## Status

| Original | Screen | Ported |
| --- | --- | --- |
| `rombank2.c` | Title + how-to-play | verified in pyboy |
| `rombank1.c` + `solar.c` | Gameplay (player, enemies, fuel, collision, music) | verified in pyboy |
| `rombank3.c` | Win / game-over | ported (end screens not yet play-tested) |

**The entire 4-bank game now fits in a single 32 KB MBC0 ROM — no bank switching
needed.** ROM banks become game states (`state`: TITLE/HOWTO/PLAY/ENDING) and the
shared bank-0 helpers (rect collision, the music player, palette/sprite fades)
are inlined. ~8 KB of tile/map/music data is extracted verbatim from the C.

Verified in pyboy: title → mode select → flame intro → gameplay (player ship,
enemies, distance counter, fuel gauge, HUD, scrolling starfield). Controls match
the original — **A** thrusts and fires (firing recoil moves you horizontally),
**B** flips facing direction.
