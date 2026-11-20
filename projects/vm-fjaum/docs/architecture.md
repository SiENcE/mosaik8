# vm-fjaum: architecture

vm-fjaum is a small monster-collecting RPG. The monsters are called Fjaum
(the word is both singular and plural). What is built today is **Chapter 1**,
the first zone of a world planned in four chapters of two zones each: a town
with three interiors, two routes, a shrine's hall, a side-view climb and its
lookout, plus the title and the battle screen.

This file describes how the project is put together. The player-facing
description is [manual.md](manual.md).

## 1. What it is

vm-fjaum is a **VM8 project**. All game logic is event-script DATA
(`scripts/*.evt.toml`), compiled to bytecode and run by the engine's one
fixed native VM8 runtime (`lib/vm/`). The normative description of the VM is
the engine's `docs/vm8-spec.md`. The project owns no game code of its own
beyond the small shell in `src/main.mos`.

Target consoles (`mosaik.toml [project] target_platforms`): `gameboy` (Game
Boy), `gameboy_color` (Game Boy Color), `sms` (Master System), `gamegear`
(Game Gear) and `pce` (PC Engine).

### Building

From the `mosaik8/` root:

```bash
python mosaik8.py build --platform gameboy projects/vm-fjaum
```

Use `gameboy_color`, `sms`, `gamegear` or `pce` for the other consoles. The
ROM is written under `projects/vm-fjaum/build/<platform>/` (for the Game Boy:
`build/gameboy/vm-fjaum.gb`). `build/` is not tracked.

A build compiles the checked-in `src/*.mos`; it does not regenerate them. After
editing the event scripts, the world or `studio.toml`, regenerate the
generated modules first (see section 2): `python tools/regen_generated.py`
refreshes every generated module of every project that is stale.
`scenes.mos` and `rooms.mos` must always be regenerated together.

### Checking

```bash
python projects/vm-fjaum/verify.py                      # Game Boy and Master System ROMs
python projects/vm-fjaum/verify.py --platform gameboy   # one console (gameboy or sms)
python tests/fjaum_chapter1_test.py                     # likewise every tests/fjaum_*_test.py
```

`--project DIR` (on `verify.py` and every test module except
`fjaum_core_data_test.py`) runs the same checks on a copy of the project, which
is how the checks are mutation-tested. `tests/run_all.py` picks up all five
test modules and `tests/project_verify_test.py` runs `verify.py`. Section 10
says what each covers.

## 2. File map

| Path | Kind | What it holds |
| --- | --- | --- |
| `mosaik.toml` | authored | project name, the five targets, the sprite sheet list, the font, `[build]` (heap size, banking, 8x16 sprites, export shaking) |
| `studio.toml` | authored | `[sprites] residency = "room"`, `[kind_sprites]`, `[audio]`, `[player]` (16x16 body, `jump = 7`), `[scenes]` (glyph text, fades, letterbox, the box hides sprites), `[emotes]`, `[scene_layout]`, `[animations.*]` |
| `world.toml` | generated + authored | the 11 scenes (index = room id), the doors, the triggers, the palette library, the `[[replace_tile]]` banks, `[world]`, the default `[tileset]`, `[kinds]`, `[kind_palettes]`, `[kind_variants]` |
| `scripts/field.evt.toml` | authored | 42 field scripts: title, rooms, people, the pause menu, encounters, the shop, saving, Hew |
| `scripts/battle.evt.toml` | authored | 19 battle scripts: the battle loop and the helpers both files call (`sp_stats`, `heal_party`, `learn_moves`, ...) |
| `scripts/dialogue.toml` | authored | every text the game shows (`[defs.*]`) and the string lists (`[lists.*]`) |
| `scripts/tables.toml` | authored (`names` generated) | the 13 data tables |
| `scripts/heap.toml` | authored | the pinned save layout and the arrays |
| `scripts/songs.toml`, `scripts/instruments.toml` | generated, then editable | the nine songs and their instruments |
| `src/main.mos` | authored | the fixed shell: boot, the text box frame, the main loop |
| `src/scripts.mos` | generated (`mosaik_vm`) | the bytecode blob, the strings, the data tables |
| `src/scenes.mos` | generated (`mosaik_scenes`) | the world: tilesets, maps, collision, palettes, objects |
| `src/rooms.mos` | generated (`mosaik_vm`) | room load, doors, triggers, actor slots, `[kind_variants]` |
| `src/glue.mos` | generated (`mosaik_vm`) | the sound and music driver wiring |
| `src/clips.mos` | generated (`mosaik_anim`) | the animation clips from `studio.toml [animations]` |
| `src/songs.mos`, `src/instruments.mos` | generated (`mosaik_vm`) | the music data |
| `src/emotes.mos` | generated (`mosaik_vm`) | the emote bubble art (`emote_exclaim`) |
| `src/uiframe.mos` | generated (the author's UI generator) | the 9-slice text box frame tiles |
| `assets/tiles_*.png` | generated | one background tileset per room (`tiles_title.png` is the world default) |
| `assets/anim_*.png` | generated | animated water tiles for Tessel, Mossway and Mosswood |
| `assets/mon_*.png` + `mon_*_back.png` | generated | 22 species, front and back view, each with a `.sprites.toml` manifest |
| `assets/<person>.png` | generated | the player and the people (16x16 frames, down / up / right; left is a flip) |
| `assets/thorns.png` | generated | the thorn bush on the Ridgeway |
| `assets/digits.png`, `battle_letters.png`, `battle_bars.png` | generated | the battle HUD tile banks |
| `assets/font.png`, `assets/frame.png`, `assets/emote_exclaim.png` | generated | the font, the text box frame, Wren's "!" bubble |
| `verify.py` | authored | the ROM checks |
| `docs/` | authored | this file and the manual |

The room art, the sprite sheets, most of `world.toml` (its header says which
parts) and the songs are produced by the author's generator scripts, kept
outside this repository, and checked in here as data. Every generated source
module carries a `GENERATED by ...` header; never edit one by hand.

## 3. Rooms

A room's index in `world.toml` is its room id: scripts address rooms by
number (`change_scene room = N`, `scene() == N`), so new rooms are only ever
appended. Sizes are in 8x8 tiles; a scene without `map_w` / `map_h` takes the
world's 20x18, and a scene without `scene_type` is `topdown`.

| Id | Name | Scene type | Size | On Init | What it is |
| :-: | --- | --- | --- | --- | --- |
| 0 | `title` | menu | 20x18 | `title` | the title screen: NEW GAME / CONTINUE |
| 1 | `tessel` | topdown | 40x18 | `field_init` | the home town; the kid strolls here |
| 2 | `mossway` | topdown | 40x32 | `field_init` | the first route: tall grass, Wren, the farmhand |
| 3 | `battle` | menu | 20x18 | `battle` | the battle screen (no player) |
| 4 | `home` | topdown | 20x18 | `house_init` | the player's house, Mom; NEW GAME starts here |
| 5 | `elder_house` | topdown | 20x18 | `house_init` | the Elder, the glowing keystone |
| 6 | `shop` | topdown | 20x18 | `house_init` | Tessel Shop, the clerk |
| 7 | `ridge` | platform | 64x18 | `field_init` | the Ridgeway: side view, three parallax bands, heath grass, the alpinist, the thorn bush |
| 8 | `mosswood` | topdown | 40x32 | `field_init` | the forest route: ferns, the forager and the pilgrim |
| 9 | `shrine_hall` | topdown | 20x18 | `house_init` | the shrine's hall, the Moss Keeper; the keystone glow palettes cycle here too |
| 10 | `ridge_top` | topdown | 20x18 | `field_init` | the lookout at the top of the Ridgeway |

The interiors come after the battle room because they were added later. The
Ridgeway is the only platform scene (`[player] jump = 7` matters only there);
its parallax bands (rows 0..4 speed 3, rows 5..9 speed 2, the rest with the
camera) are scanline bands on the Game Boy family, one plane elsewhere.

**The ways between rooms.** A room has at most four `[[door]]`s, so every edge
exit is a `[[trigger]]` whose script changes the room:

- Tessel's three house doors lead into the home, the shop and the Elder's
  house; each interior's doormat (two door rects) leads back. Tessel's north
  road is the trigger `to_mossway`.
- Mossway's four south doors return to Tessel. Its north gap is the trigger
  `to_ridge`, its west edge the trigger `to_mosswood`.
- Mosswood's east door returns to Mossway; two doors on the shrine's doorway
  lead into the hall, whose doormat leads back.
- The Ridgeway's west door returns to Mossway, its east door leads up to the
  Ridgeway top, whose two south doors lead back. The thorn bush stands between
  the Ridgeway's start and its east door.

Other triggers: the tall-grass rectangles (four in Mossway, six in Mosswood,
two on the Ridgeway, each with an On Enter and an On Leave), `wren_spot` on
Mossway's path below Wren, and `hew_gate` over the thorn bush and the air
above it.

## 4. Scripts

Field and battle are split by file, not by room: `field.evt.toml` holds
everything that runs in the title and the field rooms, `battle.evt.toml` the
battle loop plus the shared helpers that compute a member's stats, heal the
party and fill move slots. Both compile into one blob.

### Boot, title, rooms

| Script | Does |
| --- | --- |
| `main` | the boot script (`core.boot(..., scripts.ENTRY_main)`): empties the party slots, `lead` 0, 3 tonics, 5 shards, 100 gold, the home room and spot for a loss, seeds the RNG, text speed 1 |
| `title` | song 0, starts `title_glint`, a shake, then `save_exists()` decides between the menu CONTINUE / NEW GAME and NEW GAME alone. CONTINUE is `lock`, `load`, `wait 1`; then `change_scene` to the home (room 4, 112, 96). LOAD does not end the thread, so the `wait 1` lets the load's room change win; with no valid save the wait falls through to a new game |
| `title_glint` | a looping thread cycling the title letters' palette (palettes 1, 41, 42 into slot 1). A thread, not a timer, because the title menu holds the frame |
| `field_init` | outdoor rooms: starts `menu_watch`, song 1 in Tessel else 2, resets the grass counter, starts the 8-frame encounter timer; in Tessel starts `kid_walk` and, once Wren is beaten (`flags` bit 0), loads the four evening palettes (37..40) |
| `house_init` | interiors: starts `menu_watch`, songs 5 / 6 / 7 for home / Elder / shop, the `glow_tick` palette timer in the Elder's house and the shrine's hall |
| `to_mossway`, `to_ridge`, `to_mosswood` | the edge-exit triggers |

### People

| Script | Does |
| --- | --- |
| `mom_talk` | no party: points the player to the Elder; else heals the party |
| `elder_talk` | no party: the starter menu (MOSSLING / EMBERKIT / BUBBUN), sets Wren's species (`rv_sp`) to the one strong against the pick, fills party slot 0 at level 5 with a random facet and its moves; else heals the party and calls `dex_reward` |
| `kid_talk`, `kid_walk` | two tips; a looping stroll |
| `rival_talk`, `wren_spot` | Wren: without a party the spot trigger turns the player back; with one and `flags` bit 0 clear he challenges (emote, `trn = 0`, `trainer_start`) |
| `farmhand_talk`, `forager_talk`, `pilgrim_talk`, `alpinist_talk`, `keeper_talk` | one shape each: no party, "COME BACK WITH A FJAUM!"; not beaten, the emote, the challenge, `trn`, `trainer_start`; beaten, the line after |
| `shop_talk`, `shop_run`, `give_item` | `shop = 0`, the buy loop over the `shops` / `wares` tables, item ids 0 tonic, 1 shard, 2 glint shard, 3 prism shard |

### Encounters

The grass triggers COUNT: `grass_in_1/2/3` add one to `in_grass` and set
`zone` (1 Mossway, 2 Mosswood, 3 the Ridgeway), `grass_out` subtracts one
(never below 0), because the engine runs the next rectangle's On Enter before
the last one's On Leave. `encounter_tick` (timer 0, every 8 frames) rolls only
when `in_grass > 0`, the party is not empty and the player has moved since the
last tick: a `grace` countdown first (6 after every battle), else 1 in 10. A
hit walks the zone's `wild` rows by cumulative weight, picks the level
`lv + rand(240) % span`, stores the return room and position, shakes, sets
`m_sp` to the lead's species and changes to room 3.

### Trainers

`trainer_start` sets `trainer = trn + 1` (0 means a wild battle), `tslot = 0`,
calls `trainer_next` (the trainer's `tparty` row `tp + tslot`; species 254
means Wren's `rv_sp`), stores the return point, sets `m_sp` and enters the
battle room. After each won fight the battle advances `tslot`; while a next
member exists it stores the lead and RE-ENTERS room 3, so every member is a
fresh room load. The last win pays the row's gold, sets `flags` bit `flag`,
the badge bit when `badge` is not 255 (with a jingle) and a gem when `gem`
is not 0.

### The battle (`battle`)

1. Palettes for the lead (`ally_pal`) and the foe (a switch on `f_sp`);
   song 8 for a trainer, else 3; `lock`.
2. The foe: a random facet, `sp_stats`, its moves (`learn_moves` with `k = 3`
   writes `f_mv`), SEEN in the Dex. Both sides' sleep and stat stages cleared,
   the lead loaded (`party_load`), the HUD drawn (`draw_names`, `draw_hud`).
3. The turn: the command menu FIGHT / CATCH / ITEM / RUN (`battle_command`,
   a dialogue choice).
   - FIGHT: the lead's four `p_mv` slots as list-item options; an empty slot
     asks again; a spent move says so, and when every slot is spent the lead
     RAMs for free (`any_uses`). Higher SPD moves first, a tie is a coin flip.
     `my_act` / `foe_act` call `do_move`.
   - CATCH: refused against a trainer; a tier menu (SHARD / GLINT SHARD /
     PRISM SHARD), up to three shakes, then the catch roll; a caught one is
     marked OWNED and added by `add_caught`.
   - ITEM: a tonic, 20 HP to the lead.
   - RUN: refused against a trainer; 3 in 4, always with the WIND gem.
   CATCH, ITEM and a failed RUN are followed by the foe's move.
4. End of turn: poison on either side (1/8 of max HP), the MEND gem
   (1/16), then a check for a win or a loss.
5. Won: song 4, XP `f_lv * 6 + 4` to the lead, gold `f_lv * 3` from a wild
   foe; level-ups while `m_xp >= m_lv * 12` (cap 31), each recomputing the
   stats and calling `learn_new`; then refraction when the species'
   `evo_lv` is reached; then the trainer's next member or prize; a wild win
   drops a random gem 1 in 8.
6. Lost: `party_store`, `heal_party`, back to `home_room`. Otherwise:
   `party_store`, `trainer = 0`, `grace = 6`, back to the stored room.

`do_move` reads the move's `moves` row. `cat` 0 is ATK against DEF (a burn
halves ATK, a crack halves DEF), `cat` 1 is FOC against FOC. Stat stages
(-2..+2) scale by `(2 + max(0, s)) / (2 - min(0, s))`. `calc_damage` applies
the `eltab` multiplier (2 or 1/2), the element gems (5/4), the critical roll
(1 in 16, 3 in 16 with the SPARK gem; doubles the damage and flashes the
screen). `apply_fx` applies an effect to its target (`tgt` 0 the foe, 1 the
lead; effects 1..6 hit the other side, 7 and 8 the mover) through the `x_*`
temporaries, so both sides share one code path.

### Party, learning, refraction

| Script | Does |
| --- | --- |
| `sp_stats` | `q_sp`, `q_lv`, `q_fac` in; HP `hp + 3 * lv`, ATK / DEF / SPD / FOC `base + lv`, then the facet: +/-(1 + stat / 10) on its up and down stat |
| `party_load`, `party_store` | copy the lead's slot into the `m_*` battle cells and back (species, level, HP, XP, status) |
| `add_caught` | the first free slot at full HP with its facet and moves, while fewer than 3 members |
| `heal_party` | full HP, no status, every move's uses refilled, for every member |
| `learn_moves` | a member's four slots from its `learnset` rows at or under its level |
| `learn_new` | after a level-up, the moves of exactly that level: into a free slot, or "FORGET WHICH?" with the four moves as options (B keeps them all). Every Chapter 1 species has three learnset rows, so this branch is reached only by `tests/fjaum_chapter1_test.py`'s constructed four-move member |
| `refract` | three flashes, `evo_to` becomes the species, stats recomputed, the new species marked seen and owned, palette, name and HUD redrawn |

### The pause menu

`menu_watch` is a thread both room inits start: it waits until START is held
and no box or menu is open (`held_start() == 1 and ui_open() == 0`), calls
`pause_menu`, waits for the release and loops. `pause_menu` ends with `ret`:
a called script that ends without one ends its caller too, and before
2026-11-20 that stopped the watcher after the first closed menu (START then
did nothing until the next room). It is a thread rather than an
input attachment because the attachment's per-frame tick and opcode arm
would cost about 580 B of bank 0. A `lock` stops it, so START does nothing
during dialogue.

`pause_menu` locks the room and loops on PARTY / ITEMS / DEX / TRAINER / SAVE /
CLOSE (B closes too):

- `menu_party`: a card per member via `party_card`, starting at the lead
  (`k = (lead + pj) % party_n`); two texts (`menu_card`, then `menu_card2`, or
  `menu_card2_lead` for the lead), then NEXT / SOCKET (`socket`) / LEAD
  (`lead = k`).
- `menu_items`: the counts, then TONIC / SHARD / GEMS / BACK.
- `menu_dex`: four species a page as list-item options, MORE / BACK, six
  pages; an unseen entry reads `?????`.
- `menu_trainer`: gold, seen, owned (`dex_count`), badge count.
- `menu_save`: see section 6.

### Hew

The thorn bush is actor 1 of the Ridgeway (kind `thorns`); its On Init
`thorns_init` retires it once `flags` bit 6 is set. Actors do not stop the
player in this game, so the gate is the trigger `hew_gate` over the bush and
the air above it. `hew_gate`: hewn already, nothing; no MOSS badge (`badges`
bit 0), `hew_bar`; no LEAF member in any slot, `hew_need_leaf`; else `hew_ask`
and HEW / LEAVE. HEW sets bit 6 and retires the bush; a refusal walks the
player back out on the side it came from (x 344 or 392).

## 5. Data tables (`scripts/tables.toml`)

The tables compile into one blob read one cell at a time by the Read Data
Table event (`table_get`). Cells are u8 unless noted.

| Table | Rows | Columns | Read by |
| --- | :-: | --- | --- |
| `species` | 22 | `el`, `hp`, `atk`, `def`, `spd`, `foc`, `rate`, `xp`, `evo_lv`, `evo_to`, `ls` | `sp_stats` (stats), the catch roll (`rate`), refraction (`evo_lv`, `evo_to`), the learnset walks (`ls`, the species' first `learnset` row), the Dex card and Hew (`el`). `xp` is not read by any script |
| `names` (i16) | 22 | `c0`..`c3` | `draw_names`: each cell packs three letters in base 27 (0 blank, 1..26 A..Z), so a name has up to 11 letters on the battle HUD. Written by the battle art generator |
| `eltab` | 64 | `x` | `calc_damage`, row `move element * 8 + defender element`: 2 super effective, 1 neutral, 0 halved |
| `moves` | 8 | `el`, `pow`, `acc`, `eff`, `chance`, `uses`, `cat` | `do_move`, the uses checks |
| `learnset` | 66 | `sp`, `lv`, `mv` | `learn_moves`, `learn_new`; grouped by species |
| `facets` | 9 | `up`, `dn` | `sp_stats` (stat 0 ATK, 1 DEF, 2 SPD, 3 FOC; row 0 EVEN is 255 / 255, neutral) |
| `gems` | 9 | `kind`, `arg` | `party_load`: the lead's gem into `gk` / `ga` |
| `trainers` (i16) | 6 | `gold`, `flag`, `badge`, `gem`, `tp` | `trainer_next` (`tp`), the battle's prize |
| `tparty` | 18 | `sp`, `lv` | `trainer_next`: three rows a trainer; 254 = Wren's species, 255 = no more |
| `wares` | 5 | `item`, `price` | `shop_run`, row 4 is the empty ware (item 255) |
| `shops` | 4 | `ware` | `shop_run`: four rows a shop, `shop * 4 + n` |
| `wild` | 9 | `sp`, `lv`, `span`, `w` | `encounter_tick`; `w` is the cumulative weight out of 100 |
| `zones` | 4 | `first` | `encounter_tick`: a zone's first `wild` row (zone 0 is none) |

Elements index `[lists] eln`: 0 LEAF, 1 TIDE, 2 CRAG, 3 FLAME, 4 PRISM (5..7
named, unused). `eltab`: LEAF > TIDE > CRAG > FLAME > LEAF doubles, the
reverse halves, element 4 (RAM's) is neutral both ways.

Move effects (`eff`): 1 burn, 2 poison, 3 sleep (2 or 3 turns), 4 crack,
5 the target's power down, 6 the target's guard down, 7 the mover's power up,
8 the mover's guard up; `chance` is the 1-in-N roll. Gem kinds: 1 SPARK (crits
3 in 16), 2 an element gem (`arg` = the element), 3 MEND, 4 LURE (+15 to the
catch roll), 5 WIND (a sure run).

Trainer rows (`trainers` row = `trn`, `trainer = trn + 1` in battle):

| Row | Name | Gold | Flag bit | Badge | Gem | Party (`tparty`) |
| :-: | --- | :-: | :-: | --- | --- | --- |
| 0 | WREN | 50 | 0 | - | - | Wren's species, lv 5 |
| 1 | MOSS KEEPER | 300 | 1 | MOSS (bit 0) | MOSS GEM | Mossling 9, Clayfern 10, Mossback 12 |
| 2 | FARMHAND | 60 | 2 | - | - | Pebblit 4, Burrbit 5 |
| 3 | FORAGER | 80 | 3 | - | - | Budwick 6, Padling 7 |
| 4 | PILGRIM | 100 | 4 | - | - | Budwick 7, Burrbit 7, Clayfern 8 |
| 5 | ALPINIST | 150 | 5 | - | - | Screelet 10, Quarrapede 12 |

## 6. State and saving

The VM heap has 256 cells (`mosaik.toml [build] vm_heap = 256`); the game
uses 200 of them: 145 named variables, 13 of which are arrays.

`scripts/heap.toml` PINS the layout: each variable's save cell is its
position in `cells`. New variables are appended at the end; a variable may be
renamed in place; nothing is reordered or deleted (a retired name keeps its
cell). With a pinned layout the save's identity is `save_version` alone.
**Bumping `save_version` makes every existing save read as "no save"**: the
title then offers NEW GAME only. It is 2 today; version 2 moved the party,
the Dex and the move slots into arrays.

Arrays (`[arrays]`, name = length; a member's index is its party slot):

| Array | Length | Holds |
| --- | :-: | --- |
| `p_sp` | 3 | species (255 = empty slot) |
| `p_lv`, `p_hp`, `p_xp` | 3 each | level, HP, XP |
| `p_st` | 3 | status bits: 1 burn, 2 poison, 4 crack (kept after a battle until healed) |
| `p_mv` | 12 | four move slots a member (255 = empty) |
| `p_pu` | 12 | uses SPENT per move slot (0 = full) |
| `p_fac` | 3 | facet (`facets` row) |
| `p_gem` | 3 | socketed gem (0 none, else `gems` row) |
| `dex_seen`, `dex_own` | 5 each | one bit a species, 16 species a cell |
| `f_mv` | 4 | the foe's moves |
| `it_gem` | 9 | the gem bag, a count per gem id (1..8) |

Single cells: `party_n` (0 = no starter), `lead`, `gold` (max 30000),
`it_tonic`, `it_shard` / `it_shard2` / `it_shard3` (shard, glint, prism),
`rv_sp` (Wren's species), `trainer` / `trn` / `tslot`, `home_*` (where a loss
sends the player), `ret_*` (where a battle returns), `dex_rw` (Dex rewards
given, bits 0..3). `flags` bits: 0 Wren beaten (Tessel turns to evening),
1 the Moss Keeper, 2..5 the farmhand, forager, pilgrim, alpinist, 6 the thorns
hewn. `badges` bit 0 is the MOSS badge.

**Saving.** One journey. SAVE (pause menu) refuses with no party, asks
"SAVE OVER YOUR LAST JOURNEY?" (YES / NO, B is no) when a save exists, saves,
then asks `save_exists()` again: "YOUR JOURNEY WAS SAVED." or "THIS SYSTEM
CANNOT SAVE." (the PC Engine has no battery). A save holds the room, the
player's position and every heap cell; the room's own init re-creates its
threads, timers and music on CONTINUE. The save lives in battery SRAM on the
Game Boy family and in the Sega mapper's cart RAM on the Master System and
Game Gear. `vm.sram` is in `code_banks`.

## 7. Text

Every line lives in `scripts/dialogue.toml`; scripts `say` a def by name.
Names start with the speaker or place (`mom_*`, `elder_*`, `wren_*`,
`battle_*`, `menu_*`, `shop_*`, `st_*`, `hew_*`, ...). The framed box is the
full screen width: 18 characters a line, three lines at most.

`$var$` prints a heap value. `$list[var]$` prints item `var` of a list from
`[lists]`:

| List | Items | Indexed by |
| --- | --- | --- |
| `species` | 22 names, then `?????` | `f_sp`, `m_sp`, `q_sp`, the Dex's `d0`..`d3` |
| `mvn` | the 8 moves, then `-` (always last) | `mv`, `mv0`..`mv3`, `r` |
| `who` | THE FOE, YOURS | `tgt` |
| `facet` | EVEN .. WISE | `q_fac` |
| `gemn` | NONE, then the 8 gems | `q_gem`, `g` |
| `tname` | the six trainers | `trn` |
| `badge` | 8 badge names | `r` |
| `eln` | 8 element names | `q_el` |
| `dexst` | SEEN, CAUGHT | `q_n` |
| `rwn` | the four Dex rewards | `i` |
| `ware` | the four wares with prices, then `-` | `w0`..`w3` |

A menu option may BE one list item (and nothing else): the move menu and
"forget which?" (`$mvn[mv0]$`..`$mvn[mv3]$`), the Dex page
(`$species[d0]$`..`$species[d3]$`) and the shop (`$ware[w0]$`..`$ware[w3]$`).
The battle copies a slot into `mv0..mv3` as `min(slot, 8)`, so an empty slot
(255) reads `-`. Three defs are dialogue choices that write `pick`:
`battle_command`, `elder_starter_pick`, `shop_buy_menu`. The other menus are
`menu` events with their options inline.

## 8. Sprites and the battle room

`[sprites] residency = "room"`: the generated `rooms` module uploads each
room's sprite art, the shell none. Objects are 8x16 (`[build] obj_8x16`); the
Master System and Game Gear get mirrored frames baked into each kind's sheet.

The battle room places two PLACEHOLDER kinds, `foe` at (104, 16) and `ally`
at (16, 80). `world.toml [kind_variants]` turns them into a real kind at room
load, picked by a heap variable:

| Placeholder | Variable | Becomes |
| --- | --- | --- |
| `foe` | `f_sp` | `mon_<species>` (48x48 front) |
| `ally` | `m_sp` | `mon_<species>_back` (32x32 back view) |

A value outside 0..21 leaves the placeholder as it is. The variant is
resolved during the room load, BEFORE the battle's On Init runs, which is why
both ways into the battle (`encounter_tick`, `trainer_start`) set `m_sp` from
`p_sp[lead]` before changing the room (when a trainer's next member re-enters
the room, `m_sp` still holds the lead's species).
Fronts draw in sprite palette 0, back views in palette 1
(`[kind_palettes]`); the battle script loads the species' own palette into
each slot (`palette_set`), and `refract` reloads the ally's.

The HUD is background tiles the script rewrites with `bkg_tile` from the
world's `[[replace_tile]]` banks: `digits` (0..9 and a blank), `battle_letters`
(blank, A..Z) and `battle_bars` (the HP bar cells, 0..8 pixels filled). Every
name, level and bar cell has its own tile index. On the Master System and
Game Gear, closing a text box repaints the room's map without re-uploading
its tileset, so the rewritten tiles survive the first box (verify.py phase 5
checks this).

## 9. Capacity

Banking (`mosaik.toml [build]`):

- `code_banks` lists 27 modules (the VM packs, the engine packs, the generated
  modules but `scripts`, which cannot bank, and `main`): their bodies leave
  the resident image. Anything new goes into `code_banks` first.
- `bank_bytecode = true` puts the bytecode blob in a ROM bank; `vm_heap = 256`
  (the engine default is 128); `shake_exports = true` drops unused exports.

Bank 0 is 16 KB (0x4000). Measured 2026-11-20 (after Hew, the lead's card
and the pause fix):

| Console | Resident end | Bank 0 spare |
| --- | --- | --- |
| Game Boy | 0x35FF | 2,561 B |
| Game Boy Color | 0x36D7 | 2,345 B |
| Master System | 0x3CB9 | 839 B |
| Game Gear | 0x3C5F | 929 B |
| PC Engine | builds | (no bank 0 figure) |

The Master System and Game Gear are the tight ones. Past 0x4000 a GBDK build
still writes a ROM, and it shows a black screen, so read the resident end
after every change, never the build's success alone.

Other figures: the script blob is about 10.3 KB (10,334 B, under one 16 KB
bank), 231 strings (all in the first page of 256), 200 of 256 heap cells
used. Adding the Chapter 1 rooms, doors and triggers left the resident end
unchanged on all four GBDK consoles (measured).

On the Master System and Game Gear the text is drawn into tiles ABOVE the
largest room tileset (`[scenes] glyph_text`), and that background ends at
tile 192, so a bigger room tileset takes glyph slots from every room's text.

## 10. How it is checked

### `verify.py` (ROMs)

It makes its own `--debug` build of a temp copy for each console (the `.noi`
gives the symbol addresses), then plays the ROM on PyBoy (Game Boy) or the
Genesis Plus GX libretro core (Master System) and reads RAM. It skips a
console whose toolchain or emulator is missing. Each power cycle is a child
process, with the battery RAM carried across (PyBoy's `.ram`, the libretro
`.srm`). Its phases:

1. Fresh cart: NEW GAME alone, the home, SAVE refused without a Fjaum, START
   opening the pause menu again in the same room, then (party poked in, a
   walk in Tessel) saved.
2. Power cycle: CONTINUE restores room, position and every heap cell; a second
   SAVE asks before it saves over.
3. Power cycle, CONTINUE: the second save is the one that loads.
4. Fresh cart, the Elder, EMBERKIT: slot 0 of every array at level 5, full HP,
   RAM and CINDER in the first two move slots, `party_n` 1.
5. A battle: the HUD's rewritten tiles survive the first box close, FIGHT
   lists the lead's moves, "YOU USED CINDER!".
6. Every door and exit trigger of the zone-1 rooms leads to the right room
   and spot.
7. Each trainer and the Moss Keeper: walk up, A, their lines, the battle.
8. Hew: the refusals (walking and jumping) and LEAVE walk back; HEW sets flag
   bit 6; re-entered, the bush stays gone.

### The RefVM tests (`tests/fjaum_*.py`)

These run the project's own compiled blob and tables in the engine's
reference interpreter (`mosaik_vm.refvm.RefVM`), one script at a time from a
prepared heap, with text boxes dismissed and menus answered from a queue
(`fjaum_battle_test.Game`, shared by all but `fjaum_core_data_test.py`).

| Module | Covers |
| --- | --- |
| `fjaum_core_data_test.py` | `learn_moves`, `add_caught`, `heal_party`, `party_load` / `party_store`, `calc_damage` against `eltab`, the battle palettes and the back view on `m_sp`, the TRAINER page's seen / owned counts |
| `fjaum_battle_test.py` | turn order by SPD, `cat` (ATK vs FOC), move effects, the move menu (an empty slot asks again), the foe's moves, a whole battle |
| `fjaum_battle_systems_test.py` | status both ways, stat stages, the catch tiers, refraction, move uses, a whole battle |
| `fjaum_core_systems_test.py` | facets, gems and sockets, trainers by table, the Dex and the Elder's rewards, gold and the shop, learning on level-up, a whole run |
| `fjaum_chapter1_test.py` | the encounter tables, the counted grass, the Chapter 1 trainers and the Keeper, the six newer species (rows, learnsets, HUD spelling, refraction), the party order (LEAD), "forget which?", Hew and the world data it depends on |
