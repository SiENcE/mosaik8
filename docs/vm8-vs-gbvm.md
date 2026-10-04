# VM8 vs gbvm - two bytecode VMs for 8-bit games

An architecture comparison of **VM8** (`docs/vm8-spec.md`, the normative
spec; reference implementation `lib/vm/` + `mosaik_vm/refvm.py`) and
**gbvm** (GB Studio's VM, [https://github.com/chrismaltby/gbvm](https://github.com/chrismaltby/gbvm), read at commit `b386c07`).

Both make the same architectural bet: **the engine is a fixed native program and
the game is data.** Editing a game recompiles a data blob, never the engine. VM8
says so in its opening paragraph and names gbvm as the design it learned from; the
differences below are not accidents of taste, they are the places where VM8
deliberately took a different trade.

---

## 1. At a glance

| | gbvm | VM8 |
|---|---|---|
| Targets | sm83 (GBDK/SDCC). SEGA typedefs scaffolded, no z80 dispatcher body | sm83 + 65C02 (cc65), plus SMS/GG/PCE through the same C |
| Code cursor | `const UBYTE * PC` + `UBYTE bank` (far pointer) | `u16 pc`, every byte through `fetch(u16) -> u8` |
| Code space | the whole cart, via far calls | one flat blob <= 65,535 B; banking/streaming hides inside `fetch` |
| Jump targets | link-time absolute addresses | byte offsets into the blob |
| Data memory | one `script_memory[768 + 16*64]` array: >= 0 global, < 0 stack-relative | `heap[128]` plus per-thread expr stack, call stack and args, all separate |
| Engine access | raw absolute WRAM/ROM addresses from bytecode | curated engine-state id table (§5.3), ids only |
| Dispatch | `script_cmds[]` far function pointers + `args_len`, operands copied onto the CPU stack by hand-written asm | operand widths from one generated ISA table, portable C |
| Opcodes | 133 handlers over 0x01..0x95 | 95 defined |
| Threads | 16 contexts x 64-word stacks | 8 contexts x 8-cell expression stack |
| VM RAM | ~3.6 KB (`script_memory`) plus 16 x `SCRIPT_CTX` | < 1 KB total |
| Frame budget | unbounded: the frame does not advance until every thread is waitable | hard `VM_CTXS x QUANT` = 128 dispatches per frame |
| Optionality | monolithic; every opcode is always linked | packs (absent = consume operands, do nothing) plus blob-derived opcode pruning |
| Guards | essentially none | normative, §13 |

---

## 2. The code cursor, and why VM8 has a fetch seam

gbvm's context holds a real pointer plus a bank (`include/vm.h`, `SCRIPT_CTX`),
and `VM_STEP` does `SWITCH_ROM(bank)` before reading the opcode. `VM_CALL_FAR`
pushes the bank alongside the return address, so a script can span the whole
cart. Jump targets are assembler labels: `VM_JUMP` emits
`.db OP_VM_JUMP, #>LABEL, #<LABEL`, an address the *linker* fills in.

VM8's rule **R2** forbids the interpreter from ever holding a pointer into the
blob. The PC is a `u16` offset and every byte is read through `fetch`. Three
things fall out of that:

- **Banking and cart streaming become one seam.** On GB, `fetch` is where MBC5
  switches the blob's bank (`mosaik.toml [build] bank_bytecode`); on the Lynx it
  is the cart page-cache read. Neither is visible to bytecode.
- **The PC is serializable for free**, which is what makes a save that captures a
  mid-script state, and a reference VM written in Python, possible at all.
- **The blob is position-independent and relocatable.** gbvm bytecode is
  address-bearing sdas assembly, assembled and linked into the ROM; it cannot be
  loaded, moved, or compared byte for byte across builds. A VM8 blob is a pure
  byte array whose only external contract is "offset 0 is `main`".

The cost is the 64 KB ceiling on one blob. VM8 pays it deliberately: anything
larger is the host's problem, behind `fetch`.

---

## 3. Memory: one array with signed indices, vs four typed regions

gbvm has exactly one data region:

```c
UWORD script_memory[VM_HEAP_SIZE + (VM_MAX_CONTEXTS * VM_CONTEXT_STACK_SIZE)];
```

768 globals followed by sixteen 64-word thread stacks. Every operand that names
memory is an `INT16` interpreted as
`idx < 0 ? THIS->stack_ptr + idx : script_memory + idx` (the `VM_REF_TO_PTR`
macro). One number space covers globals, thread locals, call frames and
expression temporaries. That is expressive - GB Studio's compiler builds real
stack frames with `VM_RESERVE`, addresses arguments as `FN_ARG0 = -1`, and
`VM_GET_TLOCAL` reaches a thread's own base - and it is exactly the frame-layout
footgun VM8 names in §3: an off-by-one in a frame is a silent write into another
thread's stack or into a global.

VM8 splits them and sizes each for what it holds: `heap[VM_HEAP]` (128 i16, and
*the* SAVE payload), a per-thread expression stack of 8 cells whose peak depth
the compiler statically verifies, a 4-deep call stack that holds return addresses
**only**, and `arg[4]` per thread. Nothing can address another thread's memory,
because the addressing form does not exist.

### The sharpest difference: R1, no raw addresses

gbvm's RPN evaluator has `REF_MEM`, `REF_MEM_SET` and `REF_MEM_IND` tokens that
read and write an **absolute machine address** at i8/u8/i16 width, and the ISA
has `VM_GET_FAR` (read any bank + address), `VM_CALL_NATIVE` (call a far function
pointer) and `VM_ASM` (execute inline machine code out of the bytecode stream).
This is not a corner of the design, it is how GB Studio configures its engine.
From `examples/vm_lock/data/src/data/script_engine_init.s`:

```
VM_SET_CONST_INT16      _plat_min_vel, 304
```

which expands (`include/vm.i`) to an RPN stream ending in
`.R_REF_MEM_SET .MEM_I16, _plat_min_vel` - the bytecode poking a C global in WRAM
by its linker address. The platformer's whole physics table is configured that
way.

VM8's **R1** bans all of it: bytecode may name only heap indices, engine-state
ids, string ids, script offsets and pack-defined ids. The engine bridge is the
`GET_STATE`/`SET_STATE` id table (§5.3): player x/y, camera x/y and lock, scene,
`save_exists`, facing, `is_color`, `rand_seed`, `game_time`, the player speed and
collision flags, the four camera clamp edges, then pack-defined ids from 16 up.
Writes to read-only ids are ignored; unknown ids read 0 and write nothing.

That is what buys the property gbvm cannot have: **one blob runs byte-identically
on every target.** A blob that pokes `_plat_min_vel` is welded to one build of
one engine on one memory map. It is also what makes hand-authored bytecode a
legal input rather than a corruption vector (**R6**).

---

## 4. Dispatch: a hand-written asm trampoline vs a generated table

gbvm's `script_cmds[]` (`src/core/vm_instructions.c`) is a 149-entry array of
`{far function pointer, bank, args_len}`. `VM_STEP` is hand-written sm83
assembly: it switches to the script bank, indexes the table by `opcode * 4`,
copies `args_len` bytes of operands from the bytecode **onto the CPU hardware
stack**, advances `PC`, switches to the handler's bank and `rst 0x20`s into it.
Handlers are ordinary banked C functions taking `SCRIPT_CTX * THIS` plus their
operands. It is fast, and it is completely sm83-shaped: it depends on SDCC's
`OLDCALL BANKED` frame layout, which is also why the NONBANKED handlers carry
those `DUMMY0_t dummy0, DUMMY1_t dummy1` parameters.

A visible artefact of that trampoline: **dispatched operands are big-endian**
(`.db OP_VM_PUSH_CONST, #>VAL, #<VAL`, because `push de` puts the first stream
byte in the high half), while RPN immediates written with `.dw` are little-endian
and read directly by the handler. The encoding is mixed. VM8 §5.1 mandates
uniform little-endian, native order on both target CPUs.

The deeper difference is **R4, one ISA table, all consumers**. VM8 declares
opcode byte, mnemonic, operand widths, waitable flag, stack effect and owning
pack in exactly one machine-readable table (`mosaik_vm/isa.py`), from which the
toolchain generates the assembler, the interpreter's operand-length data, the
ca65 and sdas includes, and the reference VM's decoder. gbvm keeps its opcode
numbers and operand lengths in *two* hand-maintained places - the `.macro`
definitions in `include/vm.i` and the `args_len` column of `script_cmds[]` -
which must agree byte for byte or the PC desyncs. VM8 calls hand-synchronized
tables "the primary source of PC-desync bugs" and prohibits them.

---

## 5. The frame budget: the one behavioural difference a player can feel

gbvm's `script_runner_update()` runs each context for up to
`INSTRUCTIONS_PER_QUANT` (16) instructions, then round-robins, and returns
`RUNNER_BUSY` if any thread was still non-waitable when its quantum expired.
`process_VM()` in `src/core/core.c` reads that:

```c
case RUNNER_BUSY: break;      // loop again: no update, no render, no wait_vbl_done
```

Rendering, input, timers and `wait_vbl_done()` happen **only** in the
`RUNNER_DONE / RUNNER_IDLE` arm. So a script that neither waits nor ends holds
the whole machine: `1$: VM_JUMP 1$` freezes the game rather than spinning
harmlessly. This is why a GB Studio script must reach a `VM_IDLE` or a wait.

VM8's **R3** makes that unrepresentable: bytecode execution is bounded at
`VM_CTXS x QUANT` = 8 x 16 = 128 dispatched instructions per frame,
*unconditionally*, and `run_scripts()` is one step in a fixed frame order (§8.1)
that always reaches present. No byte sequence can stall a frame. The identical
loop under VM8 simply burns its quantum every frame while the game keeps running,
which is a bug you can see rather than a hang you cannot.

VM8 also fixes the frame order normatively, including what the cutscene lock does
and does not freeze: the lock freezes gameplay logic (timers, input attachments,
the player and projectile update) and does **not** freeze actor motion, music,
animation or the camera. gbvm has the same intent (`if (!VM_ISLOCKED())` guards
`events_update` / `state_update` / `timers_update`, while `camera_update`,
`actors_update` and `actors_render` run unconditionally), but as a property of
one C function rather than a specified contract.

---

## 6. Waitables: shared heritage, formalized

Both VMs implement blocking without a blocked queue, by **rewinding the PC** to
the instruction's own opcode and yielding, so the same instruction re-executes
next frame. gbvm does it ad hoc inside handlers:

```c
if (!(*A >> 8)) THIS->PC -= (INSTRUCTION_SIZE + sizeof(idx)), THIS->waitable = TRUE;
```

with each handler open-coding its own rewind distance, plus a second mechanism -
`VM_INVOKE`, which repeatedly calls a C update function through a far pointer
stored in the context (`update_fn`, `update_fn_bank`) and lets it scribble a
counter ahead of the VM stack pointer.

VM8 keeps the rewind and drops the second mechanism. Persistent re-entry state is
exactly two cells per thread, `waiting` and `scratch` (§9); the rewind distance is
implied by the encoding (`1 + operand bytes`); and **waitable ops must therefore
have fixed-width encodings** - MENU, the one variable-length waitable, records its
own opcode offset instead. That composes for free with the lock and the
scheduler: a frozen thread's PC still points at the waitable.

Two waitable semantics VM8 pins that gbvm leaves to its handlers: the exact actor
stepping loop shared by `A_MOVE_TO` and `step_all` (§9.2, clamped so it never
overshoots), and UI ownership (§9.3, `ui_owner`, with the A-edge that closes a box
**consuming** the edge so there is no double-interact in one frame).

---

## 7. Threads, handles, locks

gbvm has 16 contexts in a linked free list, byte thread IDs, `VM_BEGINTHREAD`
with thread-local args copied into the child's stack, `VM_JOIN` (spin on the
handle word until the `SCRIPT_TERMINATED` bit 0x8000 is set), `VM_TERMINATE` by
ID, and `VM_CONTEXT_PREPARE` to stage a far entry point for the attach ops.

VM8 keeps the two features that earn their keep and drops the machinery:
parameterized spawns (`THREADN`, popping into `arg[0..3]`, read back with the RPN
`ARG` token) and joinability (`HANDLE` / `HANDLE_NEXT`, which bind a **heap cell**
that reads 1 while the thread lives and 0 from the frame after it dies). No thread
IDs, so a recycled context cannot signal the wrong watcher. Where the native side
needs to kill a script it holds a `(ctx, generation)` pair, and `kill_gen` is a
no-op unless the generation still matches.

Locking: gbvm has a global `vm_lock_state` plus a per-context `lock_count`, and
while locked the runner keeps executing the same context. VM8 adds `lockowner`
**and `exempt_ctx`** - one context that still runs under the lock, which is how a
script-driven song keeps playing through a cutscene and across a room change.

Thread death is where VM8 is most explicitly a reaction to experience. §11 makes
one shared cleanup path mandatory from *every* death cause (STOP, bare RET, RAISE,
unknown opcode, external kill): release a held lock fully, clear `exempt_ctx`,
release `ui_owner` and close its box, and zero the join handle. gbvm does the lock
and handle parts inline in `script_runner_update`; the UI-ownership question does
not arise there, because a gbvm text box is not owned by a context.

---

## 8. Exceptions and scene change: near-identical, and VM8 says so

Both use the same mechanism: an opcode records `(code, params)` and ends the
thread; the main loop services it between frames. The codes even line up - gbvm
`RESET 1, CHANGE_SCENE 2, SAVE 3, LOAD 4, TERMINATE 5`; VM8 `RESET 1,
CHANGE_SCENE 2, LOAD_COMPLETE 3`.

VM8's §10 documents a 2026-08-09 correction whose resolution matches what gbvm
does: on a scene change, **kill every thread** (except the
music-exempt one). `core.c`'s `EXCEPTION_CHANGE_SCENE` arm does
`script_runner_init(FALSE)` (rebuild the whole context free list, leave
`script_memory` intact) followed by `timers_init(FALSE)` / `events_init(FALSE)`,
which is exactly VM8's `reset_scene_ui()`. Letting threads survive produced a
visible bug: the GB Studio sample's doorway script finished its walk against the
next room's actors and re-raised the change.

The one structural difference: gbvm's `SAVE` and `LOAD` are exceptions serviced by
the loop, while VM8's `SAVE`/`LOAD` are ordinary pack opcodes and only the
*completion* of a load raises (code 3), because a restored save has to land as a
scene change.

---

## 9. Optionality: packs, and pruning

gbvm is monolithic. Every opcode's handler is in the table and linked into the
ROM whether or not the game uses it, and the handler assumes its subsystem is
present. Banking keeps that affordable on a large cart.

VM8 has two independent mechanisms, and they are different things:

- **Packs (§12.3, rule R5).** Every optional capability is a callback set behind
  a `has_*` flag. With the pack unregistered the opcode still decodes, **consumes
  its operands**, and does nothing. The same blob runs degraded, never broken, on
  a target missing the capability. Defined fallbacks are part of the spec
  (`MUSIC_TONE` falls back to the SFX pack; state id 6 reads 0 with no save pack).
- **Omitting unreachable opcodes (§15.1).** A whole-program build may drop the
  handler for any opcode the shipped blob provably does not contain, derived by
  decoding *that* blob. An omitted opcode must **end the thread**, not no-op,
  because its operand reads went with it. In mosaik8's runtime this is the single
  largest code saving: the opcode switch plus the RPN evaluator was ~24% of a
  game's compiled code, and the median sample program uses 7 opcodes.

The pack rule is also why VM8 can carry an op only one console can honour
(`OVERLAY_SHOW`, the GB window curtain) without breaking the others: elsewhere it
is a defined no-op, and `OVERLAY_MOVE_TO` reports arrival immediately so a waiting
script cannot hang.

---

## 10. Robustness

gbvm assumes its own compiler produced the bytecode. `VM_STEP` indexes
`script_cmds[]` with no bounds check, so an opcode past 0x95 reads ROM as a
function pointer and `rst 0x20`s into it; the sixteen `{0,0,0}` gaps call address
0, which restarts the cart. Heap indices are unchecked pointer arithmetic. Stack
depth is unchecked. `VM_OP_DIV` is a plain C divide.

VM8 §13 makes eleven guarantees normative, of which the load-bearing ones are:
OOB heap reads push 0 and OOB writes still **pop**; actor indices clamp; a push at
capacity is dropped and a pop at depth 0 returns 0, with a depth counter that
never wraps; `CALL` at full depth is skipped **entirely** and `RET` on an empty
stack ends the thread; unknown opcodes end the thread (the runaway-PC guard, and
the mechanism §15.1 pruning relies on); DIV/MOD/RAND by zero give 0 and shift
counts mask to 0..15; and every no-op path still consumes its operands, because
PC alignment is inviolable. Guards are the contract, not a debug mode - which is
what lets the `.v8s` assembly playground accept hand-written bytecode.

---

## 11. Instruction set surface

gbvm's 133 handlers are finer-grained and include console-specific hardware: RTC
(`VM_RTC_LATCH/GET/SET/START`), SGB (`VM_SGB_TRANSFER`), the Game Boy Printer
(`VM_PRINTER_DETECT`, `VM_PRINT_OVERLAY`), the link cable (`VM_SIO_SET_MODE`,
`VM_SIO_EXCHANGE`), `VM_RUMBLE`, a scene stack (`VM_SCENE_PUSH/POP/POP_ALL`),
sprite modes, tileset loading, and a set of decomposed actor-move primitives
(`MOVE_TO_INIT/_X/_Y/_XY/_SET_DIR_X/_SET_DIR_Y`) that let its compiler build
custom movement in bytecode.

VM8 has 95 and deliberately carries **no console-specific ops at all** - the "one
blob everywhere" rule again. Where it is richer is in composition:

- **The `_E` rule.** Any numeric parameter may be an expression. The compiler
  emits the compact literal opcode when everything is literal (byte-identical with
  a pre-expression toolchain) and otherwise pushes the operands as RPN and emits
  the `_E` variant, which pops in reverse. New expression-capable opcodes must
  follow the pattern.
- **One-shot latches instead of wider operands** (`A_MOVE_OPTS`, `SHAKE_OPTS`,
  `PROJ_ANIM`, `PROJ_GROUP`). A program that never emits one stays byte-identical,
  which is how a new fidelity feature is added without taxing existing content.
- **Typed engine reads in RPN**: `ACTOR_X/Y`, `PLAYER_X/Y`, `GET_STATE`,
  `ACTOR_HP`, `SELF_SLOT`, `ARG`, `ACTOR_MOVING`. gbvm reaches the same data with
  absolute-address reads.
- A `SELF` sentinel (0xFE) in any actor operand, so one script body serves every
  instance of a kind.

One of gbvm's "tileset loading" ops is not console-specific and DID have to be
answered: `VM_REPLACE_TILE_XY` (0x5E) rewrites the pixels of whatever tile the
map holds at (x, y), which is how a GB Studio game draws a number on the
background. VM8's `BKG_TILE` / `BKG_TILE_E` (0x4D / 0x4E) do the same write with
the CELL resolved at authoring time - the map is static data, so the runtime
never reads a tilemap back - over a world-declared `[[replace_tile]]` bank
instead of a linked tileset symbol.

RPN itself is comparable: gbvm has 27 operators including inline `ATAN2`, `ISQRT`
and `RND`; VM8 puts the transcendentals in a math pack (tokens 0x60-0x6F) because
they are expensive on sm83 and rare, and keeps the bitwise group in the core.

---

## 12. Toolchain and sizing

| | gbvm | VM8 |
|---|---|---|
| Authoring | sdas macros (`vm.i`), assembled and linked with the engine | one ISA table generates assembler, disassembler, includes and reference VM |
| Static checks | none beyond the assembler | heap idx < VM_HEAP, string ids < 256, blob <= 64 KB, **RPN peak depth <= VM_STACK**, SWITCH count, THREADN n <= NARGS; all fail loudly |
| Debug artefacts | `.map` / `.noi` from the linker | `.v8s` listing plus `.map.json` (entry offsets, script names, per-instruction records) |
| Conformance | the ROM is the definition | frame-by-frame equivalence with a generated reference VM, plus worked vectors V1-V9 |

Sizing, VM8's numbers pinned to its leanest target (the Lynx, ~46 KB usable RAM),
against gbvm's:

| Constant | gbvm | VM8 |
|---|---|---|
| Contexts | 16 | 8 |
| Quant | 16 | 16 |
| Globals / heap | 768 words | 128 i16 (256 B, and the save payload) |
| Per-thread stack | 64 words, unified | 8 expr cells + 4 call slots + 4 args |
| Total VM RAM | ~3.6 KB `script_memory` plus contexts | < 1 KB |
| Blob | whole cart, via far pointers | <= 65,535 B behind `fetch` |

---

## 13. Summary: kept, changed, given up

**Shared with gbvm:** the engine-is-native / game-is-data bet; cooperative
round-robin contexts with a quantum; PC-rewind waitables; an RPN expression
evaluator; the exception mechanism for scene change and reset, including which
threads a change kills; the cutscene lock and what it freezes; parameterized
spawns and joinable threads.

**Changed:** the PC became an offset behind a fetch seam (portability, banking,
streaming, serializability); one addressable array became four typed regions; raw
memory access, native calls and inline asm were removed in favour of a curated
engine-state id table; the hand-written asm dispatcher and its two hand-synced
operand tables became one generated ISA table; the unbounded runner became a hard
per-frame budget; every optional capability became a pack with defined
absent-behaviour; guards became normative.

**Given up:** script code beyond 64 KB per blob, far calls into arbitrary banks,
the escape hatch of calling native code or poking engine globals directly, a
768-variable heap, 16 threads, deep stacks and real stack frames, thread IDs, and
every console-specific op (printer, RTC, SGB, link cable, rumble). gbvm buys real
expressiveness with those; VM8 trades them for a blob that is portable,
verifiable, prunable and safe to hand-write, and it is the right trade only
because the target is many consoles rather than one.

---

### Sources

- VM8: `docs/vm8-spec.md` (normative), `lib/vm/` (the runtime
  packs), `mosaik_vm/isa.py` (the one ISA table), `mosaik_vm/refvm.py` (the
  reference VM), `docs/vm8-playground.html`.
- gbvm: [https://github.com/chrismaltby/gbvm](https://github.com/chrismaltby/gbvm) at `b386c07` - `include/vm.h` (state and sizing),
  `include/vm.i` (the ISA macros), `src/core/vm.c` (handlers, RPN, `VM_STEP`, the
  runner), `src/core/vm_instructions.c` (the dispatch table), `src/core/core.c`
  (`process_VM`, the exception service).
- GB Studio's generated runtime, for semantics: the `build/src` folder GB
  Studio writes when it builds a project.
