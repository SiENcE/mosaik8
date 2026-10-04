# VM8 — 8-bit Game Virtual Machine Specification

**Version 1.0-draft · normative, implementation-independent.**

VM8 is a cooperative bytecode virtual machine for script-driven games on
8-bit consoles. Primary targets: the **Game Boy family** (sm83, GBDK-2020/
SDCC) and the **Atari Lynx** (65C02, cc65). The design is informed by gbvm
(GB Studio's VM), keeping what proved out there and defining away its known
failure modes.

The architectural bet is the same as gbvm's: **the engine is a
fixed native program; the game is data.** Editing a game recompiles a data
blob, never the engine. One compiled blob MUST run byte-identically on
every target; anything console-specific lives behind a host callback,
never in bytecode.

---

## Contents

1. [Terminology and conventions](#1-terminology-and-conventions)
2. [Design rules](#2-design-rules)
3. [Machine state](#3-machine-state)
4. [Boot](#4-boot)
5. [Binary format](#5-binary-format)
6. [Instruction set](#6-instruction-set)
7. [The RPN evaluator](#7-the-rpn-evaluator)
8. [Scheduler and frame loop](#8-scheduler-and-frame-loop)
9. [Waitables](#9-waitables)
10. [Exceptions](#10-exceptions)
11. [Thread death cleanup](#11-thread-death-cleanup)
12. [Host interface](#12-host-interface)
13. [Robustness rules](#13-robustness-rules)
14. [Sizing](#14-sizing)
15. [Toolchain requirements](#15-toolchain-requirements)
16. [CPU implementation notes](#16-cpu-implementation-notes)
17. [Conformance and test vectors](#17-conformance-and-test-vectors)

---

## 1. Terminology and conventions

- **MUST / MUST NOT** — required; divergence is observable by content or
  by the test suite. **SHOULD** — required to match the reference exactly;
  observable only with hand-crafted bytecode. **MAY** — free choice.
- **frame** — one iteration of the host frame loop, nominally 60 Hz.
- **thread / context** — one cooperative execution unit.
- **blob** — the bytecode byte array; all code targets index into it.
- **pack** — an optional host capability set behind a `has_*` flag (§12.3).
- All multi-byte blob values are **little-endian** (native order on both
  target CPUs). Operands are encoded in **source order**: the first
  written operand is the first byte after the opcode.
- Arithmetic is **signed 16-bit two's complement (i16)** unless stated.

## 2. Design rules

Six rules govern every decision below. They are normative: an
implementation or extension that violates one will fail conformance.

**R1 — No raw addresses in bytecode.** Bytecode may name only: heap
indices, engine-state ids, string ids, script offsets, and pack-defined
ids. There is no far-memory read/write, no native call, no inline machine
code, and no computed heap addressing. This is what makes one blob safe
and meaningful across different memory maps, and what makes hand-authored
bytecode unable to corrupt the machine.

**R2 — One code cursor: a u16 offset behind a fetch seam.** The
interpreter never holds a pointer into the blob. Every bytecode byte is
read through `fetch(u16) -> u8`. Banking (GB/MBC5) and cart streaming
(Lynx) live inside `fetch`; the PC is serializable for free.

**R3 — Deterministic frame budget.** Bytecode execution is bounded at
`VM_CTXS × QUANT` dispatched instructions per frame, unconditionally. No
byte sequence can stall a frame.

**R4 — One ISA table, all consumers.** Opcode byte, mnemonic, operand
widths, waitable flag and stack effect are declared in exactly one
machine-readable table, from which the toolchain generates the assembler,
the interpreter's operand-length data, the ca65 and sdas include files,
and the reference VM's decoder. Hand-synchronized tables are the primary
source of PC-desync bugs and are prohibited (§15).

**R5 — Byte-identical when absent.** Every optional capability is a pack.
With the pack unregistered, its opcodes consume their operands and do
nothing. The same blob runs — degraded, never broken — on a target
missing the pack.

**R6 — Guarded by default.** Hand-authored bytecode is legal input.
Every index, every stack operation, every division has defined,
non-corrupting behavior (§13). Guards are the contract, not a debug mode.

## 3. Machine state

A conforming VM holds exactly this state (names descriptive; sizes §14).

**Global:**

| Field | Type | Meaning |
|---|---|---|
| `heap[VM_HEAP]` | i16 | game variables, shared by all threads; the SAVE payload |
| `lockcount`, `lockowner` | u8, u8 | re-entrant cutscene lock + holder |
| `exempt_ctx` | u8 | context allowed to run under the lock; 255 = none |
| `pend_code`, `pend_p` | u8, (u8,u16,u16) | pending exception + parameters |
| `cur_scene` | u8 | current room id (state id 5) |
| `ui_owner` | u8 | context owning the open box/menu; 255 = free |
| `box_open`, `box_id` | u8, u8 | text-box state |
| menu state | — | `menu_open, menu_row, menu_count, menu_ids (blob offset), menu_last, cursor, nav latches` |
| `prev_a`, `a_edge` | u8, u8 | A-button edge detector (§12.4) |
| timers × NTIMERS | `active u8, period u16, count u16, entry u16` | §6 group 0x4x |
| inputs × NIN | `active u8, btn u8, entry u16, prev u8` | §6 group 0x4x |
| `player_hit_entry`, `has_player_hit` | u16, u8 | projectile player-hit script |
| pack callbacks + `has_*` flags | — | §12.3 |
| `menu_cancel`, `box_hold`, `box_hold_live` | u8, u8, u8 | one-shot UI latches (states 30 / 31), consumed when the next MENU / UI_TEXT opens; `box_hold_live` is the OPEN box's remaining countdown |
| `save_slot` | u8 | one-shot slot latch (state 40), consumed by the next save / load / clear / peek / `save_exists()` |
| `proj_group`, `proj_anim` | u8, (u8, u8, u8) | one-shot PROJ_GROUP / PROJ_ANIM latches, consumed by the next projectile launch (held by the projectile pack) |
| `move_opts` | u8 | one-shot A_MOVE_OPTS latch, consumed by the next actor move (held by the actor pack) |
| `shake_axis`, `shake_wait` | u8, u8 | one-shot SHAKE_OPTS latch, consumed by the next SHAKE (held by the player pack) |
| `fade_style`, `sprites_hidden`, `scene_update_paused`, `overlay_cut` | u8 each | persistent states 32 / 34 / 35 / 41; `scene_update_paused` is cleared per scene |
| `cam_set` | u8 | state 4's byte, GB Studio's `camera_settings` (default 3 = follow both axes) |
| `cam_dz_x`, `cam_dz_y`, `cam_off_x`, `cam_off_y` | u8, u8, i16, i16 | states 36..39: the follow camera's dead zone (per scene) and offset (global) |
| music routines × 4 | `entry u16, ctx u8, gen u8` + an attached mask | MUSIC_ROUTINE slots, busy-gated by `(ctx, gen)`; fed from the music driver's 4-deep RING of `6xy` parameter bytes (oldest dropped on overflow), drained in frame step 2 |

**Per context (× VM_CTXS):**

| Field | Type | Meaning |
|---|---|---|
| `active` | u8 | allocated and running |
| `pc` | u16 | byte offset into the blob |
| `waiting` | u8 | waitable first-entry latch (§9) |
| `scratch` | u16 | waitable scratch (one cell is enough — normative) |
| `sp`, `stack[VM_STACK]` | u8 (made it u8 so `vpush`/`vpop` index with one add), i16 | expression stack |
| `csp`, `call[CALL_DEPTH]` | u8, u16 | call stack (return addresses) |
| `arg[NARGS]` | i16 | thread arguments, read via RPN `ARG` |
| `self` | u8 | bound actor for the `0xFE` SELF operand; default 0 |
| `handle` | u8 | 255, or a heap index mirroring this thread's liveness (§11) |
| `gen` | u8 | spawn generation, incremented by every `spawn` into this context; `gen_of` / `kill_gen` (§12.2) and every busy gate compare it, so a stale handle never reaches a re-used context |

Expression and call stacks are deliberately **separate** and small
(gbvm's unified 64-word stack invites frame-layout bugs and cannot be
statically verified); thread `arg[]` and `handle` restore gbvm's two
genuinely useful thread features — parameterized spawns and joinability —
without thread IDs, frames, or pointers.

## 4. Boot

`boot(fetch, render_text, entry)` MUST establish:

- all contexts inactive; `pc=0, waiting=0, scratch=0, sp=0, csp=0, self=0,
  handle=255, arg[*]=0`;
- `lockcount=0, exempt_ctx=255, ui_owner=255, box_open=0, menu_open=0,
  prev_a=0, cur_scene=0, pend_code=0`;
- all timers and input attachments inactive; actor pool reset (§12.3
  defaults);
- **the heap is NOT cleared** — it is zero-initialized once at program
  start (BSS) and survives a re-boot / RESET exception;
- context 0 activated with `pc = entry` (by convention the script named
  `main`, offset 0).

On a double-buffered target (Lynx), boot additionally registers the
open-UI redraw hook (§12.5).

## 5. Binary format

### 5.1 The code blob

One flat byte array ≤ 65 535 bytes; scripts concatenated; every u16 code
target (JUMP/THREAD/CALL/entry/timer/input/music) is an absolute byte
offset. No header, no relocation, no alignment. Larger programs are the
host's concern: `fetch` banks or streams; the bytecode never sees more
than one 64 KB code space.

### 5.2 Encoding

`opcode:u8` + fixed little-endian source-order operands per the ISA table,
with exactly three variable-length forms:

```
RPN    (0x08): token stream terminated by 0xFF        (§7)
SWITCH (0x0C): u8 count, then count × (i16 value, u16 target)
MENU   (0x31): u8 dest_var, u8 row, u8 count, count × u8 string_id
```

Sentinels: **`0xFE` = SELF** in any actor-index operand (resolves to the
thread's bound actor; the pool is smaller than 0xFE, so no collision);
**`0xFF` = the PLAYER** in the actor operand of the ops that can answer for a
non-pool entity (`A_EMOTE` only — see §0x1x; anything else resolves it, where
it clamps to actor 0);
**`0xFF`** = RPN terminator / "none". Signed values ride bit-for-bit in
unsigned operands (velocities i8-in-u8; SWITCH values, PUSH and SET_CONST
constants i16-in-u16).

**Waitable ops MUST have fixed-width encodings** — the rewind distance is
`1 + operand bytes` (§9). MENU, the one variable-length waitable, records
its opcode offset before reading operands and rewinds to that.

### 5.3 Engine-state ids

The curated bridge — the only way bytecode touches engine memory:

| id | Field | Access |
|---|---|---|
| 0 / 1 | player x / y | RW |
| 2 / 3 | camera x / y | RW — writing pins the camera |
| 4 | camera_settings — GB Studio's own byte, flag for flag (`camera.h`): bit 0 LOCK_X and bit 1 LOCK_Y mean the camera FOLLOWS on that axis; bits 2..5 are its `preventScroll` directions X_MIN 0x04 (left), X_MAX 0x08 (right), Y_MIN 0x10 (up), Y_MAX 0x20 (down), each clamping the camera against its OWN previous position. **0 is the full pin and 3 (`camera_init`'s value) follows both** — read the polarity twice: before W7b this id was a single bool spelled the other way round. A read returns the byte with the two LOCK bits cleared while anything else owns the camera (a scripted pin, a pan, a background scroll) | RW |
| 5 | scene | R |
| 6 | save_exists — 1 when the LATCHED slot (§state 40) holds a save this build can read. Reading it CONSUMES the latch, like every other save operation | R (0 without a save pack) |
| 7 | player facing (0 dn / 1 up / 2 lt / 3 rt) | RW — a write turns the player without moving it; the next move overwrites it |
| 8 | is_color — 1 on colour hardware | R (the CONSOLE answers, not the game) |
| 9 | rand_seed — reseed the stream `RAND` draws from | W (a read is 0) |
| 10 | game_time — a free-running VM-FRAME counter, wrapping at 16 bits | R |
| 11 | player_collide — 1 = other actors block the player, 0 = it passes through | W (a read is 0) |
| 12 | player_speed — the player's movement speed, px per VM frame (clamped to ≥ 1) | W (a read is 0) |
| 13 | player_anim_speed — the player's animation MASK (255 = paused; 0 = back to the clip's own period) | W (a read is 0) |
| 14 | player_blank — GB Studio's platformer BLANK state: the pad is ignored, the horizontal is zeroed and the sprite wears its HURT set (what a PIT raises) | W (a read is 0) |
| 15 | player_blank_grav — the gravity the blank state falls under, px/frame (GB Studio's `plat_blank_grav`). Defaults to 0, so a blank player HANGS until a script drops it | W (a read is 0) |
| 25 | camera_min_x — the follow camera's clamp RECTANGLE, world px (GB Studio's `scroll_x_min`) | W (a read is 0) |
| 26 | camera_max_x — ... its right edge (`scroll_x_max`); min == max PINS the axis | W (a read is 0) |
| 27 | camera_min_y — ... its top edge (`scroll_y_min`) | W (a read is 0) |
| 28 | camera_max_y — ... its bottom edge (`scroll_y_max`). The four seed from the room's own bounds on the first write and are cleared on scene load | W (a read is 0) |
| 36 | camera_deadzone_x — the follow camera's DEAD ZONE across, px: the camera does not move at all while the focus stays within it, which is what stops a follow camera jittering. Clamped 0..40 (GB Studio's own ceiling). **Cleared on scene load** (gbvm's `camera_reset()`) | W (a read is 0) |
| 37 | camera_deadzone_y — ... down. Same clamp, same per-scene lifetime | W (a read is 0) |
| 38 | camera_offset_x — the follow OFFSET across, a SIGNED byte: the camera centres on `focus − offset`, so a positive value draws the player right of centre. **Global** — unlike the dead zone it survives a scene load, because only GB Studio's `camera_init` clears it | W (a read is 0) |
| 39 | camera_offset_y — ... down. Signed, global | W (a read is 0) |
| 40 | save_slot — which of the three save slots the NEXT save / load / clear / peek / `save_exists()` uses. A ONE-SHOT LATCH: the operation CONSUMES it, so the value is always slot 0 again afterwards and a program that never writes it keeps the single-slot behaviour byte for byte. Out of range reads as 0 | W (a read is 0) |
| 16+ | pack-defined | per pack |

Writes to read-only ids are ignored. Unknown ids read 0, write nothing.

`game_time` counts VM frames, not display frames (§R3) — the two differ per
console, so it measures GAME time and a value converted from seconds is only as
exact as that ratio. It is also the one state whose value costs something to
MAINTAIN, so a host may keep the counter only for a program whose blob reads
id 10 (the mosaik8 runtime does; a program that never reads it sees 0 and pays
nothing per frame). Nothing else in the spec depends on the counter running.

Pack ids this implementation publishes (the reference playground publishes the
same two, so its programs run verbatim):

| id | Field | Access | Pack |
|---|---|---|---|
| 16 + btn | that button HELD (§5.4 ids, so 16–23) | R | input |
| 24 | 1 while a text box or menu is up | R | UI |
| 29 | pad_held — every held button in ONE bitmask, bit = §5.4 button id (bit 0 = A .. bit 7 = RIGHT) | R | input |
| 30 | menu_cancel — ONE-SHOT: arm the NEXT menu so B cancels it (GB Studio's `.UI_MENU_CANCEL_B`). Consumed when the MENU op opens; a cancelled menu writes **−1** to its variable where a confirm writes the 0-based cursor, so `pick + 1` is GB Studio's 0 = cancelled / 1..n numbering. A scene load clears an unconsumed latch | W (a read is 0) | UI |
| 31 | box_hold — ONE-SHOT: hold the NEXT text box for n VM frames and close it with NO key press, instead of waiting for the A dismiss. GB Studio's two non-key close modes are this shape (`closeWhen: "text"` waits on `.UI_WAIT_WINDOW|.UI_WAIT_TEXT` with no button flag and then `wait_frames`; `notModal` leaves the box up for a later close), so a held box also ignores the pad. Consumed when the UI_TEXT op opens, and the countdown starts only once a typewriter reveal has finished. A scene load clears an unconsumed latch | W (a read is 0) | UI |
| 32 | fade_style — which way a fade goes, in GB Studio's own `fade_style` numbering: 0 = towards WHITE, 1 = towards BLACK. PERSISTENT, not a latch: every later FADE op (0x38) and the host's room-load auto-fade ramp that way until the next write. The host's default is black; a program's default is host configuration applied before the first fade (the mosaik8 runtime's `[scenes] fade_style`). On the GB family a white fade walks the DMG registers one shade LIGHTER per level (each 2-bit field −1, floor 0: E4 → 90 → 40 → 00), on a colour console it scales every loaded palette towards full white | W (a read is 0) | fx |
| 33 | timer_reset — restart timer `v`'s countdown (0..3): its count is set back to its period and its active flag is left alone, so a STOPPED timer stays stopped. GB Studio's `vm_timer_reset` (`remains = value`); TIMER_SET (0x40) cannot say it, because it also starts the timer and names its script. Out-of-range ids write nothing | W (a read is 0) | timers |
| 34 | sprites_hidden — 1 hides EVERY sprite, 0 shows them again. PERSISTENT across scenes, as GB Studio's `hide_sprites` flag is (`VM_HIDE_SPRITES` / `VM_SHOW_SPRITES`). The host's text-box sprite cut must restore the program's wish, never force sprites on | W (a read is 0) | video |
| 35 | scene_update_paused — 1 PAUSES the scene type's update (the player handler with its trigger scan and entity update), 0 resumes it. GB Studio's `pause_state_update` (EVENT_SCENE_UPDATE_PAUSE / _RESUME), which gates `state_update()` alone: actors, projectiles, the camera and music keep running and the player keeps DRAWING. A scene load clears it | W (a read is 0) | player |
| 41 | overlay_cut — the SCANLINE at which the window OVERLAY stops: at that line the window layer goes off and sprites come back (unless id 34 says otherwise), so an overlay covers only the TOP of the screen with the room playing below it. GB Studio's `overlay_cut_scanline` and its numbering; PERSISTENT, not a latch. The default is **150**, which is off-screen on a 144-line display and therefore means NO cut — so does any value from 144 up. A program's default is host configuration applied at boot (the mosaik8 runtime's `[scenes] overlay_cut`). The window layer and the scanline interrupt are GB-FAMILY hardware: everywhere else this is an honest no-op | W (a read is 0) | UI |

`16 + btn` is what `INPUT_ATTACH` cannot give: the attach fires on a rising
EDGE, so a bytecode auto-repeat (DAS) or soft-drop loop needs to poll whether
the button is *still* down. Id 24 lets that same loop stand down while a modal
box owns the screen.

Id 29 is the BATCHED form of 16–23: "is any of these buttons held" is one
GET_STATE plus one B_AND against a literal mask, where the per-button
spelling is one read per button OR'd together — an await-any-input poll
re-evaluates its condition every lap, so the width of the read is a
per-frame cost. A host may fold the id away (like id 10) for a program
whose blob never reads it.

### 5.4 Portable button ids

`0 A · 1 B · 2 START · 3 SELECT · 4 UP · 5 DOWN · 6 LEFT · 7 RIGHT`.
Bytecode carries ids, never console masks (Lynx: A/B = the fire buttons,
START/SELECT = Pause/Option 1).

### 5.5 Strings

The VM passes u8 ids only; content is host-side. Reference format: per
string, lines joined by `0x0A`, terminated by `0x00`; byte `0x01` + heap
index marks a `$var$` interpolation point. ASCII.
Boxes show ≤ 3 lines — the Lynx's 20×12 text grid is the binding limit.

The token is expanded at RENDER time by reading the heap cell, and the pen
advances by the value's ACTUAL digit count, not by a fixed reserve:
reserving 5 columns left a 4-column gap after a one-digit value and pushed
the line's tail off the 20-column window. A fixed 5 survives only as the
compile-time WRAP estimate, which is deliberately conservative so a line
can never overflow.

**A MENU OPTION is a string like any other and interpolates too** — GB
Studio writes prices into its choices (`Rest ($gold$ Gold)`). Whether the
choice renderer builds the token-aware walk is decided by the OPTIONS
alone, never by the string table as a whole: a host that gates it on "any
string has a token" makes every dialogue game pay for a menu renderer it
can never use.

## 6. Instruction set

Groups 0x0x–0x1x are the **core** (mandatory). Groups 0x2x+ are engine
groups backed by packs; targets without the pack still decode and consume
operands (free via the ISA table).

Notation: `f8`/`f16` read an operand and advance `pc`; `push`/`pop` act on
the current thread's guarded expression stack. Statuses: **CONT** (counts
against QUANT), **YIELD** (thread done this frame), **END** (thread died →
§11). "rewind n" = `pc -= n` after operands were read.

### 0x0x — control and flow (core)

| Op | Mnemonic | Operands | Semantics |
|---|---|---|---|
| 00 | STOP | — | `active=0`; END |
| 01 | JUMP | t:u16 | `pc=t`; CONT |
| 02 | IDLE | — | YIELD for exactly one frame |
| 03 | WAIT | n:u8 | waitable countdown (§9.1). WAIT n yields on n frames (WAIT 1 yields exactly once, gbvm's `wait_frames`); WAIT 0 does not yield |
| 04 | LOCK | — | `lockcount+=1; lockowner=cur`; CONT |
| 05 | UNLOCK | — | if `lockcount>0`: `lockcount-=1`; CONT |
| 06 | THREAD | t:u16 | `spawn(t)`, args zeroed; CONT |
| 07 | THREADN | t:u16, n:u8 | pop n values (last-pushed → `arg[n−1]`), spawn with them; n>NARGS: extras popped and dropped; pops happen even if the pool is full; CONT |
| 08 | RPN | tokens…,FF | evaluate (§7); CONT |
| 09 | SET_CONST | i:u8, v:u16 | if `i<VM_HEAP`: `heap[i]=v`; CONT |
| 0A | SET_VAR | i:u8 | `v=pop()`; if `i<VM_HEAP`: `heap[i]=v` (**pop even when OOB**); CONT |
| 0B | IF | t:u16 | `v=pop()`; if `v==0`: `pc=t` (branch-if-false); CONT |
| 0C | SWITCH | table (§5.2) | `v=pop(); n=f8()`; read **all** n entries (pc always ends past the table); jump to the first match, else fall through; CONT |
| 0D | CALL | t:u16 | return addr = pc after operand; if `csp<CALL_DEPTH`: push + jump; else **skip entirely** (no push, no jump); CONT |
| 0E | RET | — | if `csp>0`: pop into `pc`; CONT. Else `active=0`; END |
| 0F | RAISE | code:u8, a:u8, b:u16, c:u16 | `pend_code=code; pend_p=(a,b,c); active=0`; END (§10). `CHANGE_SCENE room,x,y` is assembler sugar for `RAISE 2, room, x, y`; see `CHANGE_SCENE_E` (0x16) for a computed room |

### 0x1x — state, player, handles (core)

0x18, 0x19 and 0x1A are the exception to the grouping: all three are actor ops,
but they arrived after 0x2E/0x2F filled the actor group, and each row says so.

| Op | Mnemonic | Operands | Semantics |
|---|---|---|---|
| 10 | SET_STATE | sid:u8 | `v=pop()`; state write per §5.3; CONT |
| 11 | PLAYER_SETPOS | x:u16, y:u16 | player pack write; CONT |
| 12 | PLAYER_SETPOS_E | — | `y=pop(); x=pop()`; player write; CONT |
| 13 | HANDLE | i:u8 | bind the current thread: if `i<VM_HEAP`: `handle=i; heap[i]=1`; else `handle=255`. §11 zeroes the cell on death. CONT |
| 14 | HANDLE_NEXT | i:u8 | one-shot latch: applies HANDLE to this thread's next successful spawn (parent watches child); CONT |
| 15 | SELF | a:u8 | `self=a` if `a<NACTORS`; CONT |
| 16 | CHANGE_SCENE_E | — | `y=pop(); x=pop(); room=pop()`; then exactly as `RAISE 2, room, x, y`: `pend_code=2; pend_p=(room,x,y); active=0`; END (§10). The stack-arg form of the scene change, for a room that is COMPUTED rather than literal (a scene stack: store `scene()`, return to it later) |
| 17 | PLAYER_BOUNCE | h:u8 | upward impulse in a gravity scene: 0 low / 1 medium / 2 high, scaled off that scene's own jump strength. No gravity handler → no-op; CONT |
| 18 | A_STOP_UPDATE | i:u8 | kill actor `i`'s long-lived On Update thread (its other slot scripts are untouched); a no-op with no entity pack registered. ACTOR-scoped despite the encoding: the 0x2x group was full when it was added; CONT |
| 19 | A_PUSH | i, tiles:u8 | shove actor `i` up to `tiles` whole tiles AWAY from the player — along the PLAYER's facing — stopping BEFORE the first solid cell, then start a non-blocking walk to that cell (so it SLIDES). Terrain comes from the room's collision probe; a room with none never blocks. Also actor-scoped for encoding reasons only; CONT |
| 1A | A_EMOTE | i, id:u8 | WAITABLE: pop emote bubble `id` over actor `i` and hold the thread until it clears (60 frames). Exactly ONE bubble exists at a time — a second A_EMOTE replaces it. No emote pack registered → CONT immediately (an honest no-op). Actor-scoped for encoding reasons only; rewind 3 while up. **`i = 0xFF` is the PLAYER** and is NOT resolved (resolve would clamp it to actor 0 and put the bubble over slot 0); the bubble then follows the player's own position |
| 1B | A_SET_FRAME_E | i:u8 | as A_SET_FRAME (2E) with the frame POPPED off the expression stack, clamped to 0..254 there. Actor-scoped for encoding reasons only — the 2x group is full; CONT |
| 1C | A_REACTIVATE | i:u8 | bring a RETIRED actor back exactly where the room left it: same position, same tile base, and the clip it was given (`A_DEACTIVATE` remembers it). GB Studio's own ACTIVATE takes nothing but the actor; `A_ACTIVATE` (20) is the SPAWN form and re-seeds tile + position from literals a script cannot know. Actor-scoped for encoding reasons only; CONT |
| 1D | A_AWAIT_MOVE | i:u8 (0xFE ok) | WAIT until the actor's native auto-move (`A_MOVE_START`/`_E`) lands: while `is_moving`, rewind 2 + YIELD; else CONT. Pairs with a move start into the waitable move (arrival-timed at any speed — a fixed-frames wait breaks the moment the actor's speed changes). Deactivating the actor cancels its move, so a mid-flight kill releases the waiter the same frame. In the 0x1x group (0x2x is full) |
| 1E | PLAYER_MOVE_TO | x:u16, y:u16, mode:u8 | WAITABLE player WALK, `A_MOVE_TO`'s twin: step the player toward (x, y) at its own walk speed once per frame; while it has not arrived, rewind 6 + YIELD, else CONT. `mode` is the axis order — 0 both at once, 1 horizontal first, 2 vertical first. The step CLAMPS to the target, so a speed that does not divide the distance still lands (otherwise the rewind never ends). The facing follows the step, so an animated player walks rather than slides. Coordinates are u16 because the player's are WORLD pixels — the actor pool's u8 `A_MOVE_TO` cannot address a wide room. Collision is NOT consulted (GB Studio's move-to defaults `useCollisions` off: a scripted walk is choreography). The stepping body lives in the `player` pack, not the interpreter |
| — | *(position operands)* | — | `A_ACTIVATE` / `A_SET_POS` / `A_MOVE_TO` / `A_MOVE_START` take **u16** x/y, and the `_E` variants keep the full 16 bits they pop. An actor's position is a WORLD pixel and a room runs past 255 — a u8 sent every scripted move in a big room to a wrapped destination (2026-08-14) |
| 1F | A_MOVE_OPTS | mode:u8, coll:u8 | ARM the next actor move with GB Studio's `moveType` + `useCollisions`/`collideWith`. A one-shot LATCH rather than new operands, so `A_MOVE_TO` / `A_MOVE_START` / `_E` keep their encodings and a program that emits none is byte-identical. `mode`: 0 both axes at once, 1 horizontal first, 2 vertical first — an authored route around scenery needs it, because moving both axes at once cuts the corner through. `coll`: bit 0 stop at WALLS (the destination is CLIPPED to just before the first solid tile, in the axis order), bit 1 stop at other ACTORS and the player (tested per frame; a hit ENDS the move where it stands, as GB Studio's does, so a waiting script continues instead of pushing forever). The latch is consumed by a non-blocking move and released when a blocking one finishes; the stepping lives in the `actor` pack, not the interpreter |

### 0x2x — actors (pack: actor)

Every actor operand passes `resolve(i) = self if i==0xFE else (0 if i>=NACTORS else i)`.
(The one exception is `A_EMOTE`, which takes `0xFF` = the PLAYER and must skip
the resolve — see §0x1x.)

| Op | Mnemonic | Operands | Semantics |
|---|---|---|---|
| 20 | A_ACTIVATE | i,tile (2×u8), x,y (2×u16) | activate with pack defaults; CONT |
| 21 | A_SET_POS | i (u8), x,y (2×u16) | teleport; CONT |
| 22 | A_MOVE_TO | i (u8), x,y (2×u16) | waitable walk: one clamped step per execution (§9.2); not arrived → rewind 6, YIELD |
| 23 | A_SPEED | i,s | pixels/frame; CONT |
| 24 | A_MOVE_START | i (u8), x,y (2×u16) | non-blocking walk (native `step_all` advances); CONT |
| 25 | A_SET_CLIP | i,k | animation clip; CONT |
| 26 | A_SET_POS_E | i | `y=pop(); x=pop()` (the full 16 bits); CONT |
| 27 | A_MOVE_START_E | i | `y=pop(); x=pop()`; CONT |
| 28 | A_DEACTIVATE | i | retire (the clip is remembered for A_REACTIVATE, 1C); CONT |
| 29 | A_SET_GROUP | i,g | collision group; CONT |
| 2A | A_SET_HP | i, hp:u16 | set HP; CONT |
| 2B | A_DAMAGE | i, amt:u16 | HP = max(0, HP−amt); CONT |
| 2C | A_SET_DIR | i, d:u8 | facing 0–3; CONT |
| 2D | A_SET_COLLISION | i, on:u8 | `on != 0` = the actor BLOCKS the player's movement; CONT |
| 2E | A_SET_FRAME | i, f:u8 | pin the actor to frame `f` of its current state+facing and stop the clip advancing. Released when the actor's state or facing changes. Stored `f+1` so 0 means unpinned, hence `f <= 254`; CONT |
| 2F | A_SET_ANIM_SPEED | i, s:u8 | `s` is a MASK, not a period (GB Studio's animation speed: advance when `time & s == 0`), so the period is `s+1`; `s == 255` means never and becomes a 255-frame period, the closest a u8 holds; CONT |

### 0x30–0x37 — UI, and the three that could not fit their own group

(packs: text box, choice renderer, framed box, HUD — plus
PLAYER_KNOCKBACK, PROJ_GROUP and A_SET_ANIM_STATE, which belong to the
player / projectile / actor groups and live here because 0x1x, 0x6x and
0x2x are full.)

| Op | Mnemonic | Operands | Semantics |
|---|---|---|---|
| 30 | UI_TEXT | id:u8 | waitable modal text box (§9.3); rewind 2 while open or while another thread owns the UI. If state 31 (`box_hold`) was armed, the open consumes it and the box closes ITSELF after that many VM frames, ignoring the pad (the countdown runs after any typewriter reveal) |
| 31 | MENU | blob (§5.2) | waitable modal choice; rewinds to its own opcode offset; confirm → `heap[dest]=cursor` if `dest<VM_HEAP`. If state 30 (`menu_cancel`) was armed, the open consumes it and a B EDGE (B armed as "held" at open, like A) closes the menu the same way writing `heap[dest] = −1` |
| 32 | HUD_SHOW | on:u8 | HUD visibility; CONT |
| 33 | SHMUP_SCROLL | pace,dir (2×u8) | pack op (player): auto-scroll pace (0 = pause) and direction (0 h / 1 v / 2 keep / 3 endless loop); CONT |
| 34 | PLAYER_VISIBLE | on:u8 | show (1) / hide (0) the PLAYER's sprite — GB Studio's `ACTOR_FLAG_HIDDEN` on `actors[0]`: the DRAW stops and nothing else does, so the player keeps its position, its collision and its handler. Deliberately NOT the player-less teardown a menu room does, which a script could not undo. BOTH draw paths must honour it (the renderer and the animator's per-frame re-assert). Cleared on scene load; CONT |
| 35 | PLAYER_KNOCKBACK | — | pack op (player, PLATFORM): throw the player AWAY from its facing and upward, ungrounded, ignoring the pad for a configured number of frames (GB Studio's platformer-plugin `KNOCKBACK_STATE`, whose `state_enter_knockback` sets `plat_vel_x/_y` and re-enters itself until a timer runs out — which is how it locks input out). NO OPERANDS: the velocities are ENGINE FIELDS there, so they are configured once via `player.set_knockback` (studio.toml `[player] knockback_x/_y/_frames`); unconfigured → no-op; CONT |
| 36 | PROJ_GROUP | g:u8 | latch the NEXT launch's OWN collision group — distinct from its MASK (what it hits). The struck actor's On Hit reads it as thread ARG 0, which is how one script answers "hit by group 1" and "hit by group 2" differently. Groups are BITS (player 0x1, group 1 0x2, group 2 0x4, group 3 0x8). A one-shot latch like PROJ_ANIM, consumed by the next PROJ_LAUNCH_*; 0/unset → the runtime reports a sentinel instead, because ARG 0 = 0 means the PLAYER'S BODY. No pack → CONT |
| 37 | A_SET_ANIM_STATE | i, st:u8 (bit 7 = play once) | pin actor `i`'s clip STATE, overriding the movement-derived one (GB Studio's `VM_ACTOR_SET_ANIM_SET`, which re-points which of a sprite's named animation SETS the actor draws from — the tile sheet is unchanged, so this is a clip-state select, not a kind or sheet swap). Stored `st+1` so 0 means unpinned. A state with no frames for the current facing still falls back to idle. **Bit 7 of `st`** = play the state ONCE and hold its last frame (four states need three bits, so the top one is free); clear = loop. GB Studio never asks for one-shot — its event carries only `{actorId, spriteStateId}` and its compiler always emits `VM_ACTOR_SET_FLAGS actor, 0, ACTOR_FLAG_ANIM_NOLOOP`, which CLEARS that flag (`flags &= ~(mask & ~flags)` with `flags = 0`) — so the bit is an authoring option of ours. Actor-scoped for encoding reasons only — the 2x group is full; CONT |

### 0x38–0x3F — effects and camera (packs: fx, camera, scroll)

| Op | Mnemonic | Operands | Semantics |
|---|---|---|---|
| 38 | FADE | dir,frames (2×u8) | waitable 4-level fade. First entry: `waiting=1; scratch=frames`. Each execution: decrement scratch (floor 0); `lvl = min(3, (frames−scratch)·4/frames)` (frames 0 → 0); dir 1 (in) inverts: `lvl = 3−lvl`; host `set_level(lvl)`. `scratch>0` → rewind 3, YIELD; else final level (dir 1 → 0, dir 0 → 3), CONT. A host that auto-fades the outgoing room on a scene change (the mosaik8 runtime's `[scenes] fade`) must NOT re-ramp a screen this op has left at level 3 - it starts the arrival fade from black, as gbvm's `fade_out()` returns at once when already faded |
| 39 | SHAKE | frames,amp | start a screen shake for `frames` DISPLAY frames, amplitude `amp` px (jitter `[-amp, amp-1]`, GB Studio's own range). It is an OFFSET on whoever owns the scroll, not a scroll write of its own (GB Studio's `scroll_offset_x/y`, folded into the one scroll update): every camera owner adds it, including the parallax band shadow. Both axes WRAP, as the register does — clamping the sum makes the shake one-sided wherever the camera sits at 0. Cleared on scene load. Axis and blocking come from the SHAKE_OPTS latch; unlatched it is vertical and returns at once; CONT or YIELD |
| 3A | CAM_MOVE_TO | x,y,step (3×u8) | waitable pan; one step per execution; rewind 4 until arrival; first entry holds the camera |
| 3B | SCROLL_BG | vx,vy (2×i8) | scroll velocity, ¼ px/frame; (0,0) = off + camera release; CONT |
| 3C | PLAYER_MOVE_TO_E | mode:u8 (pops y, x) | PLAYER_MOVE_TO (0x1E) to COMPUTED coordinates: the same waitable walk, stepping via the player pack once per frame. A rewound re-entry cannot re-read popped operands, so the target LATCHES on FIRST entry (the context's `waiting` flag, the A_EMOTE shape) into one engine-state pair — one pair, not per context, because there is one player — and the rewind covers only opcode + mode (2). Exists because GB Studio's `EVENT_ACTOR_MOVE_RELATIVE` on the player is always computed (`player_x() + dx`) and its runtime WALKS it. `mode` = moveType axis order, as 0x1E. Arrival clears `waiting`; a scene change kills the thread and the spawn path re-zeroes the flag |
| 3D | A_VISIBLE | i:u8, on:u8 | show (1) / hide (0) a POOL actor's sprite - the same `ACTOR_FLAG_HIDDEN` as 0x34, on an actor instead of the player. DRAW ONLY: the actor stays in the live list, so it keeps its position, its collision box and its On Interact answer, and only the render pass stands down. That is what makes an actor hidden in its On Init an invisible INTERACTION HOTSPOT, which is GB Studio's usual way to put a script on a spot of floor - it is NOT `A_DEACTIVATE` (0x28), which retires the actor out of every scan. Orthogonal to active: `A_REACTIVATE` does not un-hide, exactly as GB Studio's `vm_actor_activate` does not clear the flag. A separate op from 0x34 rather than a widened one because dispatch pruning makes an unused arm free while a shared op's extra operand is not; measured. Cleared on scene load (every slot starts drawn); CONT |
| 3E | TEXT_SPEED | speed:u8, ff:u8 | TYPEWRITER reveal speed - GB Studio's `text_draw_speed`: 0 = instant (the engine default, so a program that never runs this op keeps the whole-box draw), 1..7 = one printable char per 1/1/2/4/8/16/32/64 DISPLAY frames (`frame & mask == 0` over its own ui_time_masks row, derived as `(1 << (speed-1)) - 1`, never a resident table). `ff` = A or B HELD fast-forwards; the same press cannot also dismiss (read_input's dismiss stands down while a reveal runs, and a held button no longer edges next frame). GLOBAL state, persisting across boxes and scenes as GB Studio's does. The reveal itself runs in OP_UI_TEXT's wait path, paced on elapsed system.frames() capped at 8 (the music-catch-up clock), through the `core.set_text_step` seam - the generated `render_text_step(id, from, upto) -> drawn` plots printable chars [from, upto) and returning fewer than asked is the completion signal. Unregistered, or on the present-redraw consoles (Lynx/PCE, whose box redraws whole every present), text stays instant; CONT |
| 3F | TEXT_BLIP | freq:u16, frames:u8 | the per-character BLIP beside 0x3E - GB Studio's `text_sound`: a square tone played through the g_tone seam once per reveal step that drew at least one char (their per-char sfx, folded to the step so a fast reveal does not machine-gun the channel). freq 0 = off (their SFX_STOP_BANK). Silent without vm.snd registered, like every sound op; CONT |

### 0x4x — timers, input, save, and the group's overflow tenants

Timers/input are core-adjacent and mandatory; save is a pack. 4A–4E are
lodgers from full groups (the overlay curtain, the shake latch, and the
background tile write) — **an opcode's group says where the room was, not
what it does**, which is a recurring fact about this table rather than an
accident here.

| Op | Mnemonic | Operands | Semantics |
|---|---|---|---|
| 40 | TIMER_SET | id:u8, period:u16, entry:u16 | `id<NTIMERS`: `active=1, count=period`. Per **unlocked** frame: decrement; at 0 → `count=period`, `spawn(entry)` **iff the slot's last spawned instance has ended** (the busy gate — GB Studio `timers_update`'s SCRIPT_TERMINATED check; a tick while it still runs fires nothing). First fire after `period` frames; period 0 fires every frame |
| 41 | TIMER_STOP | id:u8 | deactivate |
| 42 | INPUT_ATTACH | btn:u8, entry:u16 | first free slot **or** the slot already bound to btn (re-attach replaces); `prev` = the button's *current* held state (a held button must release before firing). A press while the slot's last spawned instance is still running fires nothing (the busy gate — GB Studio `events_update`; a shoot script's cooldown `wait` is a real fire-rate limit). The gate is released by the instance's death (the §11 path), on scene change, and on kill(). **Bit 7 of `btn` is the OVERRIDE flag** (GB Studio's `input_slots[i] & 0x80`): the button is CONSUMED, i.e. the native player handler must not see it while the attachment is live — its `events_update()` does `joy ^= key` and runs *before* `state_update()`. Buttons are 0..7, so the flag rides the operand and an attachment without it is byte-identical. The consumed set is released by INPUT_DETACH and by `reset_scene_ui` |
| 43 | INPUT_DETACH | btn:u8 | deactivate every slot bound to btn |
| 44 | SAVE | — | save pack: persist heap + engine snapshot into the LATCHED slot (§state 40); CONT |
| 45 | LOAD | — | save pack: restore the LATCHED slot; success → the pack raises exception 3 (§10); no valid save → no-op; CONT |
| 46 | SET_PLAYER_HIT | entry:u16 | register the projectile player-hit script |
| 47 | DATA_CLEAR | — | save pack: clear the LATCHED slot's signature, so it reads as empty (the payload bytes are left alone); CONT |
| 48 | DATA_PEEK | src:u8, dst:u8 | save pack: read heap cell `src` out of the LATCHED slot without loading it and write it to `dst`; a slot with no valid save writes 0; CONT |
| 49 | MUSIC_ROUTINE | slot:u8, entry:u16 | Attach a script to one of the FOUR hUGE **call-routine** slots (GB Studio's `EVENT_SET_MUSIC_ROUTINE` / gbvm's `vm_music_routine`): a `6xy` cell in a hUGETracker module then runs it. `slot` is masked `& 3`, as the reference masks it. The driver calls a thunk from its TIMER INTERRUPT, which only ENQUEUES the effect's parameter byte into a 4-deep ring (oldest dropped on overflow); the MAIN LOOP drains it in the same unlocked block as the timers, so a cutscene lock freezes it. Of the queued byte, `& 3` picks the slot and `>> 4` is the argument, delivered as `arg(0)`. **BUSY-GATED per slot**: a spawn only once the previous instance has ended (the INPUT_ATTACH rule, for the same reason). **An UNATTACHED slot consumes its item and ABORTS the rest of that frame's drain** - gbvm does the same (`return`, not `continue`), and VM8 behaves alike on purpose. The registration dies with the scene (gbvm's `music_init_events` on every CHANGE_SCENE). **hUGE ONLY**: the portable driver has no per-cell effect column at all, so this is a no-op wherever `[audio] gb = "huge"` is not in force; CONT |
| 47-49 | *(assigned)* | — | this block held the turn-based battle pack (BATTLE_OPEN / BATTLE_CLOSE / BATTLE_ATTACK), removed 2026-09-17; W7c took 47 and 48, W7h took 49, so it is spent. 5F and 66+ are the free encodings |
| 4A | OVERLAY_SHOW | row:u8 | fx pack: the window OVERLAY CURTAIN (GB Studio's `ui.c`) — a solid cover on the GB **window** layer, positioned in tile ROWS (0 = whole screen covered, 18 = gone). NOT a fade: the palettes stay bright and the scene is revealed row by row. GB family only; elsewhere a no-op; CONT |
| 4B | OVERLAY_MOVE_TO | row, speed (2×u8) | **waitable** — slide the curtain to `row`, one row per `ui_time_masks[speed] + 1` display frames. Off the GB family it reports ARRIVED immediately, so a waiting script cannot hang |

| 4C | SHAKE_OPTS | axis, wait (2×u8) | one-shot LATCH consumed by the next SHAKE (the A_MOVE_OPTS shape): `axis` is GB Studio's CAMERA_SHAKE_X (1) / CAMERA_SHAKE_Y (2) mask, `wait` makes SHAKE block until the frames run out (its `camera_shake_frames` sets `ctx->waitable`). Unlatched, SHAKE keeps this engine's vertical, non-blocking shake, so a program that emits none is byte-identical; CONT |
| 4D | BKG_TILE | dst, src (2×u8) | Rewrite background tile `dst`'s 16 bytes from the world's `[[replace_tile]]` bank (GB Studio's `VM_REPLACE_TILE` / `EVENT_REPLACE_TILE_XY`). Every map cell holding that index redraws at once - the indirection is the point, because a changed MAP cell is undone by the next scroll or room warm-up while changed PIXELS survive both. That is how a game with no HUD layer draws a NUMBER on the background (the RPG check conversion spells its wallet as four cells and writes a digit under each). `dst` is a tile INDEX, resolved where the write is authored - the map is static data, so the runtime never reads a tilemap back. Routed through the opt-in `core.set_bkg_tile` seam (`scenes.replace_tile`, which also handles the SMS/GG per-tile palette); a no-op unregistered, so a world with no bank links none of it. CONT |
| 4E | BKG_TILE_E | dst:u8; pops src | The `_E` twin of 4D and the COMMON case: GB Studio's own `tileIndex` is a ScriptValue and a digit readout computes it (`gold % 100 / 10`). `dst` stays inline - a map cell is a place, not a value. An out-of-bank `src` is a no-op, because the arithmetic that produced it is a script's, not the world author's. CONT |
| 4F | PAL_SET | layer, slot, pal (3×u8) | Write one palette of the world's `[[palette]]` LIBRARY into one HARDWARE palette slot at run time: `layer` 0 = sprite / 1 = background, `slot` 0..7, `pal` = the library index. GB Studio's four palette events are all this one primitive there too — `paletteSetSprite` / `paletteSetBackground` are a mask of chosen slots, `paletteSetUI` is background slot 7 and `paletteSetEmote` is sprite slot 7, each a `_paletteLoad` + inline colours. The operand is an INDEX rather than 12 bytes of colour because a library entry is already layer-correct (a sprite palette holds the authored colours `[c0, c0, c1, c3]` — colour 2 skipped, because sprite entry 0 is hardware-transparent). Routed through the opt-in `core.set_pal_write` seam (`scenes.set_palette`, emitted for a world that declares `[world] pal_write`); a no-op unregistered. The write goes through the palette VERB, not the hardware, so a fade's shadow records it and a later ramp cannot undo it. A console with one palette per layer (DMG, NES) makes that verb an honest no-op, so a recolour is silently nothing there. CONT |

### 0x5x — sound and music (packs: sound, music voice, driver, transport)

| Op | Mnemonic | Operands | Semantics |
|---|---|---|---|
| 50 | SFX | id:u8 | canned effect |
| 51 | TONE | hz:u16, frames:u8 | tone on the SFX channel |
| 52 | SND_STOP | — | stop the SFX channel |
| 53 | MUSIC_PLAY | entry:u16 | script-driven song: if `exempt_ctx≠255` kill it; `exempt_ctx = spawn(entry)` — the song thread runs even under the cutscene lock (§8.2) |
| 54 | MUSIC_STOP | — | kill `exempt_ctx` if set; driver stop; silence the music voice (fallback: SFX stop) |
| 55 | MUSIC_TONE | hz:u16, frames:u8 | music voice; fallback: SFX tone |
| 56 | MUSIC_SONG | id:u8 | play song #id via the driver pack |
| 57 | MUSIC_PAUSE | — | transport pack: silence the driven song and KEEP its position |
| 58 | MUSIC_RESUME | — | transport pack: resume a paused song from where it stopped |
| 59 | MUSIC_MUTE | mask:u8 | transport pack: per-channel MUSIC mute, bit `c` SET = channel `c` muted (gbvm's `VM_MUSIC_MUTE` polarity: GB Studio's Mute Channel event lists the ACTIVE channels and clears their bits out of 0x0F). Persistent until the next MUSIC_PLAY, which clears it - as gbvm's `vm_music_play` resets its `music_global_mute_mask` - so it does NOT survive a song change |
| 5A | A_CLEAR_ANIM_STATE | i:u8 | RELEASE actor `i`'s A_SET_ANIM_STATE pin (stored 0 = unpinned): the clip state is derived from movement again, idle standing and walk moving. GB Studio's set-state to the sprite's DEFAULT state (`''` = `statesOrder[0]`, loaded by `vm_actor_set_anim_set`); pinning state 0 instead would hold the idle clip on a walking actor. A lodger - the actor groups are full - and a second op rather than a sentinel in 37's operand, which every set-state user would pay for. CONT |
| 5B | THREAD_STOP | i:u8 | END the live thread whose join handle (HANDLE 13 / HANDLE_NEXT 14) is heap cell `i`, with the §11 cleanup (its cell reads 0). No such thread is a no-op, as a finished handle is in gbvm's `VM_TERMINATE`; the executing thread stopping itself ends like STOP. GB Studio stores a context id in the handle variable where ours keeps liveness, hence the lookup. `i ≥ VM_HEAP` is a no-op. CONT (END when it stopped itself) |
| 5C | A_SET_BOX | i, w, h, ox, oy:u8 | replace actor `i`'s collision BOX: size (w, h) and offset inside the drawn sprite (ox, oy), the box its projectile hits, pushes and player blocking use. `w = 0` retires it. The same write the host makes at room load from a kind's hitbox; GB Studio's `vm_actor_set_bounds` (EVENT_ACTOR_SET_COLLISION_BOX). CONT |
| 5D | A_GET_DIR | i, v:u8 | `heap[v] = ` actor `i`'s FACING, 0 down / 1 up / 2 left / 3 right (the numbering of state 7 and A_SET_DIR). GB Studio's `vm_actor_get_dir` (EVENT_ACTOR_GET_DIRECTION), which numbers down/right/up/left; a converter maps it. An opcode rather than an RPN read because an unused token still grows the RPN dispatch (measured: 5 B of SMS/GG bank 0 per non-user). `v ≥ VM_HEAP` writes nothing. CONT |
| 5E | A_START_UPDATE | i:u8 | START actor `i`'s On Update script from the top when it is not running, through the host's entity registry; nothing when it runs already or the actor has none. GB Studio's `vm_actor_begin_update` (EVENT_ACTOR_START_UPDATE). A host that parks off-window updates may leave a PARKED actor for its wake, which respawns the script anyway. The stopping half is A_STOP_UPDATE (18). CONT |

### 0x6x — projectiles (pack: projectiles)

| Op | Mnemonic | Operands |
|---|---|---|
| 60 | PROJ_LAUNCH | x:u16, y:u16, vx:i8, vy:i8, tile:u8, life:u8 — default mask 0xFE |
| 61 | PROJ_LAUNCH_E | tile:u8, life:u8; pops vy, vx, y, x |
| 62 | PROJ_LAUNCH_M | as 60 + mask:u8 |
| 63 | PROJ_LAUNCH_EM | tile:u8, life:u8, mask:u8; pops vy, vx, y, x |
| 64 | PROJ_LAUNCH_A | tile:u8, life:u8, mask:u8; pops speed, angle, y, x — fire along an ANGLE (0 up, 64 right, 256 to the turn) at `speed` in 1/16 px per frame. No pack → CONT, nothing fired |
| 65 | PROJ_ANIM | frames:u8, period:u8, stride:u8 — animate the NEXT launch: `frames` frames from the launch tile, each `stride` tiles after the last (a converted 8x16 shot cell is 2 tiles), stepped every `period` display frames, wrapping (GB Studio's loopAnim + animSpeed — `period = animSpeed + 1`, their `anim_tick` mask). A one-shot latch: consumed (armed or not) by the next PROJ_LAUNCH_*, cleared on scene change and by projectile.reset(). period/stride 0 → 1. No pack → CONT, static tile |

### The `_E` rule

Any numeric parameter may be authored as an expression. The compiler emits
the compact literal opcode when all parameters are literals (**byte-
identical** with a pre-expression toolchain) and otherwise emits the
operand expressions as RPN pushes in argument order followed by the `_E`
variant, which pops in reverse. New expression-capable opcodes MUST follow
this pattern.

**Unknown opcode:** `active=0`, END — the runaway-PC guard. Never skip:
without a known length there is nothing safe to skip to.

## 7. The RPN evaluator

An RPN token stream is evaluated inline within the instruction's budget
slot, all values i16, on the thread's private stack. Binary operators pop
right first: `b=pop(); a=pop(); push(a op b)`.

| Byte | Token | Immediate | Effect |
|---|---|---|---|
| 01 | PUSH | u16 (i16 bits) | +1 |
| 02 | VAR | u8 heap idx | +1 (OOB → push 0) |
| 03 / 04 | ACTOR_X / ACTOR_Y | u8 actor (0xFE ok) | +1 |
| 05 / 06 | PLAYER_X / PLAYER_Y | — | +1 |
| 07 | GET_STATE | u8 id | +1 (§5.3) |
| 08 | ACTOR_HP | u8 actor (0xFE ok) | +1 |
| 09 | SELF_SLOT | — | +1 |
| 0A | ARG | u8 n | +1 (`arg[n]` if `n<NARGS`, else 0) |
| 0B | ACTOR_MOVING | u8 actor (0xFE ok) | +1 (1 while the actor's native auto-move is in flight, else 0; deactivating the actor cancels the move, so the poll `while actor_moving(n) { wait 1 }` is the waitable-move idiom) |
| 10–14 | ADD SUB MUL DIV MOD | — | −1 · **DIV/MOD by 0 → 0** |
| 15–19 | B_AND B_OR B_XOR SHL SHR | — | −1 · shift counts masked to 0–15 |
| 20–25 | EQ NE LT GT LE GE | — | −1 (signed; 0/1) |
| 30 / 31 | AND / OR | — | −1 (strict binary — both operands already pushed, no short-circuit; 0/1) |
| 32 | NOT | — | 0 (0/1) |
| 40 / 41 | MIN / MAX | — | −1 (signed) |
| 42 / 43 / 44 | NEG / ABS / B_NOT | — | 0 |
| 50 | RAND | u16 bound | +1 (`random() % bound`; bound 0 → 0) |
| 51 | ATAN2 | — | −1 (binary, args pushed y then x: pushes the ANGLE of the vector (x, y) — 0 up, 64 right, 256 to the turn, y in SCREEN sense. A math PACK implements it; unregistered → pushes 0) |
| 60–6F | unassigned | none | no token is defined in this range; an implementation treats one as unrecognized (below) |
| FF | END | — | terminate |

An **unrecognized token terminates evaluation** (treat as END). The
bitwise group is included in the core (flag variables are ubiquitous);
the one transcendental, ATAN2 (0x51), keeps its token in the core table
but its BODY in the math pack (registered through `set_atan2`; unregistered
it pushes 0), because it is expensive on sm83 and rare. No other math token
exists (there is no ISQRT), and nothing is assigned in 0x60-0x6F.

The toolchain MUST statically verify each stream's peak stack depth
≤ `VM_STACK` (§15); the runtime overflow guard (§13) is a safety net,
never a path reached by compiled content.

## 8. Scheduler and frame loop

### 8.1 Normative frame order

```
each frame:
  1  read_input()                    ; edge detect; may dismiss the text box (§12.4)
  2  if lockcount == 0:
        tick_timers()
        tick_input_attach()
        music_events_update()        ; drain the music-routine ring (§6 MUSIC_ROUTINE); same block as the timers
  3  run_scripts()                   ; the only bytecode execution point
  4  if pend_code: service_exception()             ; §10
  5  if lockcount == 0: player pack; projectile update
     (an implementation MAY run the projectile update regardless of the lock
      when the program opts in - `[build] proj_under_lock`, gbvm parity)
  6  actor step_all + render         ; motion is lock-immune
  7  animation packs; projectile render; music driver update; HUD update
  8  camera apply (scripted cam / shake / scroll — lock-immune)
  9  present (wait vblank)
 10  [double-buffered targets only] settle-redraw of open UI (§12.5)
```

Normative freeze semantics: the lock freezes gameplay logic (steps 2, 5)
and does **not** freeze motion, music or visuals (steps 6–8) — a cutscene
stops the game, not the world. Timers freezing under the lock is load-
bearing: a short-period timer during a long cutscene would otherwise spawn
frozen threads until the context pool is exhausted.

### 8.2 Scheduler

Round-robin contexts `0..VM_CTXS−1` in index order; skip inactive. Under a
lock run **only** `lockowner`, plus `exempt_ctx` if different (script-
driven music plays through cutscenes). Per scheduled thread: execute until
YIELD, END, or `QUANT` CONT results. Hard bound: `VM_CTXS × QUANT`
dispatched instructions per frame (R3). An implementation MAY additionally
cap RPN tokens per instruction at 255 as a runaway guard.

### 8.3 spawn

Find the **lowest** inactive context (return 255 if none; callers ignore
failure). Set `pc=entry, active=1`; **reset** `waiting, scratch, sp, csp,
self` to 0 and `handle` to 255; load `arg[]`; if the spawner holds a
HANDLE_NEXT latch, transfer it (bind the child, write 1 to the cell, clear
the latch). The `csp` reset is normative — a context killed mid-CALL must
not leak its return stack into the next script that reuses it.

## 9. Waitables

No blocked queue. An unfinished waitable **rewinds `pc` to its own opcode
byte and returns YIELD**; next frame the same instruction re-executes,
re-reads operands, re-checks. Persistent re-entry state is exactly two
cells per thread — `waiting` and `scratch` — which composes for free with
the lock and the scheduler: a frozen thread's pc still points at the
waitable and resumes exactly where it was.

### 9.1 WAIT (normative)

```
first entry (waiting==0):  waiting=1; scratch=n
every execution:           if scratch>0: scratch-=1; rewind 2; YIELD
                           else:         waiting=0; CONT
```

Test-before-decrement, so WAIT n yields on exactly n frames. This is
gbvm's `wait_frames` timing (it seeds n+1 and decrements first; seeding
n+1 here would wrap a u8 at n=255, so the test moves instead). Corrected
2026-09-01: the original spelling decremented before testing, which made
WAIT n yield n-1 times and WAIT 1 fall straight through - the shortest
expressible wait never suspended, and an await-input loop built on WAIT 1
spun its whole quantum.

### 9.2 Actor stepping (exact — shared by A_MOVE_TO and step_all)

```
for axis in x, y:
    if pos < target:  pos = target if target-pos < speed else pos+speed
    elif pos > target: pos = target if pos-target < speed else pos-speed
arrived = (pos_x == tx and pos_y == ty)
```

One step per execution/frame; diagonals step both axes (Chebyshev);
clamped so it never overshoots. `step_all()` runs every frame (lock-
immune) for each actor with `moving=1`, clearing `moving` on arrival.

### 9.3 UI_TEXT / MENU ownership

First entry: if `ui_owner` is another live context → rewind, YIELD
(retry). Else take `ui_owner`, set `box_open`/`menu_open`, draw. While
open → rewind, YIELD. The A-edge in `read_input()` closes the box and
**consumes the edge** (no double-interact in the same frame). MENU
additionally arms both nav latches to "held" — the press that opened the
menu must release before it navigates or confirms; cursor clamped to
`[0, count)`; confirm writes `heap[dest]`, releases the UI, sets
`prev_a = 1` (the confirming press is spent for §12.4 too: the menu may
read the pad after `read_input()` sampled it, and the held A must not edge
into the box the script opens next), CONT. A menu
opened with the `menu_cancel` latch (§5.3 id 30) armed also arms B, and a B
edge closes it exactly like a confirm with `heap[dest] = −1`.

## 10. Exceptions

Scene change, reset, and load-complete are one mechanism: `RAISE` records
`(code, a, b, c)` and **ends the raising thread** (§11 then releases a
held lock — under a lock only the owner runs, so the raiser is the owner).
Step 4 of the frame services it:

| code | Name | Service |
|---|---|---|
| 1 | RESET | kill **all** threads; `reset_scene_ui()`; heap NOT cleared; re-activate context 0 at `entry` |
| 2 | CHANGE_SCENE (room=a, x=b, y=c) | kill every thread **except `exempt_ctx`** (the tracked song thread plays across rooms); `cur_scene=a`; fade level → 0; `reset_scene_ui()`; scene-change pack `(a,b,c)` |
| 3 | LOAD_COMPLETE | as code 2, with room/x/y from the restored state (raised by the save pack inside LOAD) |
| other | — | cleared and ignored (forward compatibility) |

Codes 2 and 3 killing the other threads is a **2026-08-09 correction**
(they used to survive "by design", on the theory that the room loader
kills per-room threads — it never could; it only resets entity SLOTS,
and a fire-and-forget thread belongs to no slot). A surviving thread is
bound to a room that no longer exists, and the failure is not abstract:
the GB Studio sample's path→town doorway script is a waitable walk-to
followed by `CHANGE_SCENE`, and its surviving copies finished their walk
against the NEXT room's actors and re-raised the change — Sample Town
visibly "reset" moments after arriving, repeatedly.

This is also what **GB Studio's own engine** does, which settles it:
`core.c`'s `EXCEPTION_CHANGE_SCENE` arm kills every thread but keeps the
variables — `script_runner_init(FALSE)`, which rebuilds
the whole context free-list while leaving `script_memory` intact —
followed by `timers_init(FALSE)` / `events_init(FALSE)`, i.e. exactly
our `reset_scene_ui()`. Each of our kills runs the full §11 death path,
so held locks / UI latches / join handles release; the one exemption is
`exempt_ctx`, the tracked song thread, so music plays across rooms.

`reset_scene_ui()` - one normative list: close box + menu; **clear the two
one-shot UI latches, `menu_cancel` and `box_hold`, and the open box's
`box_hold_live` countdown with them** (§5.3 ids 30/31: a latch belongs to the
room that armed it - the arm and its MENU / UI_TEXT are adjacent in a
lowering, but the open REWINDS for as long as another thread owns the UI, so
the two can be frames apart and a scene change can land between them);
`ui_owner=255`;
cancel UI redraw; hide overlays; deactivate **all** timers and **all**
input attachments, and with them their busy gates (no instance) and the
INPUT_ATTACH override / consumed-button mask (the new room's player gets
its buttons back); clear the player-hit registration; drop all four
music-routine registrations (gbvm's `music_init_events(FALSE)` on every
scene change). State a ROOM LOAD resets instead (the player pack's hidden
flag, the emote bubble, the projectile pack's latches, the camera dead
zones) is the room loader's, not this list's.

Only one exception can be pending; a later RAISE in the same frame
overwrites (last-writer-wins). Native code may raise the same exceptions
via `request_change()` — doors need no bytecode.

## 11. Thread death cleanup

When a context dies for **any** reason — STOP, bare RET, RAISE, unknown
opcode, external `kill(ctx)` — one shared path MUST run:

1. if it is `lockowner` and `lockcount>0`: `lockcount = 0` (full release);
2. if it is `exempt_ctx`: `exempt_ctx = 255`;
3. if it is `ui_owner`: `box_open=0; menu_open=0; ui_owner=255`;
4. if `handle < VM_HEAP`: `heap[handle] = 0`.

Centralizing this is what makes it safe for *any* opcode to end a thread:
a `lock … stop` script must not freeze the machine, and rule 4 is the join
mechanism — `HANDLE_NEXT h; THREAD child` lets the parent poll `heap[h]`
(1 = running, 0 = done) with no thread IDs and no way for a recycled
context to signal the wrong watcher.

## 12. Host interface

### 12.1 Required callbacks

| Callback | Signature | Contract |
|---|---|---|
| `fetch` | `(u16) -> u8` | blob byte; the banking/streaming seam (R2). Reads are sequential per thread between jumps |
| `render_text` | `(u8 id)` | draw string id into the bottom text box |
| `random` | `() -> u16` | frame-random source |
| `held` | `(u8 btn) -> bool` | portable-button sampling (§5.4) |

**Implementation note (mosaik8, 2026-09-02) — the code window.** `fetch`
stays the contract, but a host MAY additionally hand the VM the blob's
address plus an enter/leave pair (`core.set_code_window(base, enter,
leave)`; the generated `scripts.code_window()` does, on every console but
the cart-streaming Lynx). Inside a scheduler slice the interpreter then
reads bytes INLINE at `base + pc` and, where the blob lives in a switchable
ROM bank, maps that bank once per slice (`enter` returns the bank to
restore) instead of saving, switching and restoring per byte. Everything
outside a slice (the menu's option ids) still goes through `fetch`.
A RESIDENT blob registers the window without the pair
(`core.set_code_window_res(base)`, 2026-09-05): the generated
`scripts.code_window()` forks on the build-stated `VM_CODE_BANKED`
(`[build] bank_bytecode`), because two seam calls that did nothing cost a
slice ~620 T-cycles on the sm83 - a fifth of a one-instruction WAIT slice
(the falling-block assembly sample's hold-left `run_scripts` 116.4k -> 116.0k; a banked blob is
unchanged, OAM-identical). `tests/code_window_test.py` pins both forms.
Measured on the Game Boy (the `tools/framebudget/` harness): the
per-byte cost was never the interpreter's bulk — sdcc's switch layout was
(a compare ladder for the RPN tokens until the `0xFF` terminator test moved
out of the switch, see `lib/vm/core.mos`); together the two took
the falling-block assembly sample's idle VM frame from 2 LCD frames to 1.

### 12.2 API the VM must expose

`spawn(entry) -> ctx|255` · `kill(ctx)` · `gen_of(ctx) -> u8` /
`kill_gen(ctx, gen)` (spawn stamps a per-context GENERATION; kill_gen is a
no-op unless it still matches, so a holder of a long-lived handle - an
entity pack's On Update thread - can never kill an unrelated thread the
pool put in a context its script vacated on its own) · `set_self(ctx,
actor)` (valid indices only) · `interact_pressed() -> 0|1`
(`a_edge && !box_open && !menu_open`) · `request_change(room,x,y)` ·
`var_get(i) -> i16` / `var_set(i,v)` (save packs, `$var$` interpolation) ·
`scene() -> u8` · `fire_player_hit()` · `run_pending()` (one scheduler pass
over the active threads, outside the frame loop - the generated room load
runs it over the freshly spawned actor On Init threads BEFORE spawning the
scene's own init script, GB Studio's load ordering: a hide/set-pos On Init
lands while the screen is still faded out, and a short one ENDS and frees
its context; spawned beside a scene init whose first event is `lock`, they
sat frozen for the whole cutscene, since under a lock only the owner runs).

### 12.3 Packs

Callbacks + `has_*` flag, default off. Off ⇒ opcodes consume operands, do
nothing — with these defined fallbacks: `MUSIC_TONE`/`MUSIC_STOP` fall
back to the SFX pack if only that is registered; state id 6 reads 0
without a save pack.

Packs: actor pool · player · scene-change · fx (fade 0–3 / shake) ·
camera · scroll · text box · framed box · choice renderer
`(string_id, row, selected)` · HUD · redraw-behind-UI · animation ·
save (persist/restore/has) · sound (sfx/tone/stop) · music voice ·
music driver (play/update/stop) · music transport · projectiles
(launch/update/render) · math (the body of RPN token 0x51 ATAN2, registered
through `set_atan2`; unregistered, the token pushes 0).

Actor-pool defaults (normative for conformance): per slot `active, x, y
(u16 world px, §6 position operands), tile, speed (default 1), moving, tx,
ty, clip (255=none), group
(default 2), hp (u16, default 1)`. `activate` resets group and hp and
fixes speed 0 → 1; `deactivate` clears active/moving/clip.

### 12.4 Input edge and the text box

Each frame: `a_edge = held(A) && !prev_a`. If `box_open && a_edge`: close
the box, release `ui_owner`, clear the box region, **consume the edge**.

### 12.5 Double-buffered UI redraw (Lynx-class)

Where present recomposites the frame, an open box/menu (and HUD) MUST be
redrawn: (a) for a few frames after every UI change (reference countdown
4) to seed both flip pages, and (b) from inside present whenever the frame
is re-blitted. Menu option ids are re-read from the blob at `menu_ids` via
`fetch`. Persistent-tilemap targets (GB) MUST NOT redraw per-frame — it
tears; draw once at open/change.

## 13. Robustness rules

A conforming VM MUST NOT corrupt memory or hang on **any** byte sequence:

1. heap access guards `idx < VM_HEAP`; OOB reads push/return 0; OOB
   writes still pop;
2. actor indices ≥ NACTORS (other than 0xFE) clamp to 0; `set_self`
   accepts only valid ctx/actor;
3. expression-stack push at capacity is **dropped**; pop at depth 0
   returns 0; the depth counter never wraps;
4. CALL at full depth is skipped entirely; RET on an empty stack ends the
   thread;
5. unknown opcodes end the thread; unknown RPN tokens end the evaluation
   (an implementation that omits unreachable handlers relies on this — §15.1);
6. every no-op path (absent pack, guarded index) still consumes its
   operands — PC alignment is inviolable;
7. DIV/MOD/RAND by zero produce 0; shift counts mask to 0–15;
8. the `VM_CTXS × QUANT` budget bounds per-frame execution
   unconditionally;
9. thread death always runs §11, from every path;
10. spawn resets all per-context state (§8.3);
11. unrecognized exception codes are cleared, never looped.

## 14. Sizing

Uniform values, pinned to the leanest target (the Lynx: ~46 KB usable RAM
for code + data + everything). One size set means one blob, one compiler
check, and no "works on GB, corrupts on Lynx" bug class. A build MAY raise
them, but the toolchain MUST verify content against these minimums:

| Constant | Value | Bytes | Note |
|---|---|---|---|
| `VM_CTXS` | 8 | — | threads |
| `QUANT` | 16 | — | CONT results / thread / frame |
| `VM_HEAP` | 128 | 256 | i16 variables; the SAVE payload |
| `VM_STACK` | 8 | 128 | expr cells / thread (statically verified) |
| `CALL_DEPTH` | 4 | 64 | return addresses / thread |
| `NARGS` | 4 | 64 | thread args / thread |
| `NACTORS` | 8 | ~120 | pool (player separate) |
| `NTIMERS` | 4 | ~28 | |
| `NIN` | 8 | ~48 | |
| blob | ≤ 65 535 B | — | u16 PC |
| strings / scenes | ≤ 256 each | — | u8 ids |

Total VM state < 1 KB. Timing: the hard bound is 128 dispatched
instructions/frame. (The "~60 cycles average dispatch" this paragraph once
assumed is not what a C-hosted interpreter achieves: with every byte read
through the `fetch` callback the mosaik8 runtime measured ~2,500 ticks per
operand fetch on the Lynx before caching the pc in a register, and a per-byte
bank switch on the GB costs more still. At ~60 cycles per dispatch the bound would be < 45% of an
sm83 frame worst-case (≈17 500 M-cycles at 60 Hz) and a rounding error on
the Lynx's 4 MHz 65C02; a hand-written core (§16) can get there. For the C
runtime the fetch path, not the opcode bodies, is the budget.)

## 15. Toolchain requirements

- **One ISA table** (R4) declaring per opcode: byte, mnemonic, operand
  widths (or `varlen`), waitable flag, stack effect, owning pack.
  Generated from it: assembler + disassembler, the interpreter's
  operand-length data, `vm8.inc` (ca65) and `vm8.i` (sdas/GBDK), and the
  reference VM's decoder.
- Compile-time hard caps, failing loudly: heap indices < VM_HEAP; string
  ids < 256; blob ≤ 65 535 B; RPN peak depth ≤ VM_STACK; SWITCH count
  ≤ 255; THREADN n ≤ NARGS.
- Emit a text assembly listing (`.v8s`) and a debug map (`.map.json`:
  entry offsets, script names, per-instruction records) — every
  implementation's disassembler and symbol table.
- Per-platform script filtering MUST keep the heap layout identical across
  targets (seed variable order from the full program) so saves and tooling
  agree everywhere; a filtered-out script compiles to a 1-byte STOP stub
  so every entry symbol stays defined and references no-op.
- The literal path MUST stay byte-identical when expression support or new
  optional operands are added (the `_E`/`_M` pattern).

### 15.1 Omitting unreachable opcodes (optional)

An implementation MAY omit the handler for any opcode the loaded program's
blob does not contain. A whole-program build knows its blob, and a typical
game uses well under a quarter of the ISA, so on a tight target the unused
handlers are the single largest block of dead code. (In mosaik8's runtime the
opcode `switch` and the RPN evaluator were ~24 % of a game's compiled code;
measured 2026-09-21 across the 62 `projects/` blobs that have scripts, the
median program uses **7 of 101** opcodes, the largest 55; an earlier count
on the 67-opcode ISA of its day read 7 of 67.)

Rules, if an implementation does this:

1. **The toolchain owns the decision.** The set MUST be derived from the
   exact blob being shipped, by decoding it (a blob is a pure instruction
   stream, so a linear walk is exact), never from authoring-level guesses.
   Deriving it from a *different* build's blob is a conformance violation.
2. **Omission is not a no-op.** An omitted opcode MUST behave as §13 rule 5 —
   *end the thread* — and MUST NOT fall through and continue. It cannot
   consume its operands (the operand reads are in the omitted handler), so
   continuing would desync the PC. This is deliberately the fail-stop choice:
   if a derivation is ever wrong, the game halts a thread rather than
   executing garbage.
3. **It is invisible to conforming programs.** By construction an omitted
   opcode cannot appear in the blob, so behaviour is unchanged. It follows
   that an implementation MUST NOT omit handlers if it can load a blob it did
   not see at build time (a downloadable level, a REPL, a generic player) —
   there, the full ISA is mandatory.
4. **Nothing else changes.** Sizing (§14), the binary format (§5) and every
   other rule are unaffected; blobs stay portable between a pruned and a full
   implementation.

This is distinct from an **absent pack** (§12.3), where the opcode *is*
implemented, consumes its operands, and does nothing. A pack is absent
because the host lacks a capability; an opcode is omitted because the
program provably never executes it.

## 16. CPU implementation notes

Informative. The reference interpreter is portable C (SDCC and cc65);
these notes target hand-written cores.

**sm83 (Game Boy, GBDK-2020/SDCC).** Dispatch via a 256-entry jump table
(512 B, `jp hl`); keep the unknown-op default. Inline `fetch`; it is where
MBC5 switches the blob's bank — restore the caller's bank on exit and
never hold a blob pointer across a yield. No signed compare: implement
EQ/LT/… via subtraction with sign/overflow handling; DIV/MOD as the
16-bit restoring loop with the zero check **before** the loop. Keep the
current context's fields behind one base pointer or a fixed WRAM window.

**65C02 (Atari Lynx, cc65).** Put the per-context hot fields (pc lo/hi,
sp, csp, waiting, self) in zero page as struct-of-arrays indexed by
`X = ctx`; the flat expression-stack array (128 B) also fits ZP. Dispatch
via split lo/hi tables + `jmp (abs)` or the RTS trick. `fetch` is the cart
page-cache read — the u16 PC is exactly what makes streamed bytecode
possible. Claim zero page through the linker config; respect cc65's own
ZP usage (`regbank`, pointer temps). All VM BSS counts against the ~46 KB
MAIN budget — the sizing table of §14 is chosen to make that painless.

**Both.** Operand widths come from the generated table, never from memory;
the rewind distances are implied by the encoding. Two invariants to test
first in any new core: the SWITCH fall-through pc (past the *whole*
table), and the WAIT frame counts (V2 below).

## 17. Conformance and test vectors

Conformance = frame-by-frame observable equivalence with the reference VM
(generated against the same ISA table): heap contents, actor
positions/activity/HP, ordered logs of opened strings, menu selections,
sound ops and exceptions, and lock/UI status. ROM harnesses (PyBoy for GB,
Mednafen/libretro for Lynx) re-verify native builds.

Worked vectors (blob bytes → expected observable result):

**V1 — arithmetic.** `09 00 05 00` (SET_CONST heap[0]=5) ·
`08 02 00 01 03 00 10 FF` (RPN: VAR 0, PUSH 3, ADD) · `0A 01`
(SET_VAR heap[1]) · `00` → after frame 1: `heap[0]=5, heap[1]=8`, thread
dead.

**V2 — WAIT timing.** `03 02 00`: yields frames 1 and 2, dies frame 3.
`03 01 00`: yields frame 1, dies frame 2.
`03 00 00`: dies frame 1 without yielding.

**V3 — lock leak.** `04 00` (LOCK, STOP): after frame 1, `lockcount==0`
and other threads runnable.

**V4 — handle.** `14 03` (HANDLE_NEXT 3) · `06 t t` (THREAD): `heap[3]`
reads 1 while the child runs, 0 from the frame after its STOP.

**V5 — args.** `08 01 07 00 FF` (push 7) · `07 t t 01` (THREADN, 1): in
the child, `RPN ARG 0` pushes 7.

**V6 — exception teardown.** A lock-holding thread runs
`0F 02 04 28 00 3C 00` (RAISE 2, room 4, x 40, y 60): same frame — thread
dead, lock released, its box closed; step 4 sets `cur_scene=4`, resets
timers/inputs/UI, calls the scene pack (4, 40, 60), and **kills every
other context except `exempt_ctx`** — so a sibling thread does NOT run
the next frame (§10; corrected 2026-08-09, it used to survive).

**V7 — guards.** `0A 90` (SET_VAR 144): pops, writes nothing.
`08 13 FF` on an empty stack: two underflow-zeros popped, DIV-by-zero
pushes 0. Opcode `FF`: thread ends, §11 runs.

**V8 — budget.** `01 00 00` at offset 0 (JUMP 0): exactly QUANT dispatches
for this thread this frame; the frame presents on time; other threads
still receive their quanta.

**V9 — SWITCH fall-through.** `08 01 05 00 FF` (push 5) ·
`0C 02  01 00 aa aa  02 00 bb bb` (SWITCH, 2 cases: 1→aa, 2→bb): no
match; pc ends immediately after the table; execution falls through.
With push 2, pc = bb.
