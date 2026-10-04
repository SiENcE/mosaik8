"""mosaik_vm.py -- the VM8 event compiler + assembler + reference interpreter.

The toolchain half of the reference-engine-style VM. It sits beside mosaik_scenes.py and turns authored EVENT
LISTS (`scripts/<owner>.evt.toml`, the reference engine's project-file analogue, one committed/diffable
file per owner) into VM bytecode, exactly as mosaik_scenes.py turns a world into
a `scenes` module. The three lowerings:

    .evt.toml  --(catalogue)-->  .vms text asm  --(assembler)-->  bytecode
                                                                     |
                                       src/scripts.mos (CODE blob + fetch + strings)
                                       scripts.map.json (blob offset -> event, debug)

Files stay the truth: the event lists are readable text, the `.vms` text form is
a first-class artifact power users can hand-author, and `scripts.mos` is
deterministic + headless-reproducible like `scenes.mos`.

The opcode numbering + operand shapes MUST match lib/vm/core.mos's OP_* switch
(the interpreter that runs the bytecode on a real console). To let event
semantics be unit-tested WITHOUT a ROM, this module also ships RefVM -- a Python
reference interpreter that mirrors vm.core's scheduler/waitables/lock exactly, so
tests drive frames and assert on actor positions / heap / text.

Stage-1 scope mirrors the Stage-0 spike's opcodes (STOP JUMP IDLE WAIT LOCK
UNLOCK THREAD SET_CONST + actor ACTIVATE/SET_POS/MOVE_TO/SPEED + UI_TEXT). RPN
expressions, CALL/RET, the engine-state bridge and the native-op packs are the
concept's next stages -- new catalogue entries + new OPS rows, no structural
change.

CLI:
    python mosaik_vm.py scripts/ -o src/scripts.mos       # a folder of *.evt.toml
    python mosaik_vm.py demo.evt.toml -o src/scripts.mos  # a single file
"""

# This package was split out of the former single-file mosaik_vm.py. The public
# API is unchanged: `import mosaik_vm` still exposes every symbol (EVENTS,
# Compiler, CompiledProgram, RefVM, compile_path, generate_*, scaffold_project,
# ...) at the top level, so all existing callers keep working. Submodules hold
# the implementation; this file re-exports their namespaces flat.

from . import (  # noqa: F401  (re-exported)
    isa, fmt, events, dialogue, sound, songs, projio, compiler, asm, instruments, loader, glue, rooms, hud, emotes, huge, uge, refvm, scaffold, cli
,
)

_SUBMODULES = (isa, fmt, events, dialogue, sound, songs, projio, compiler, asm, instruments, loader, glue, rooms, hud, emotes, huge, uge, refvm, scaffold, cli)

for _m in _SUBMODULES:
    for _k, _v in vars(_m).items():
        if not _k.startswith('__'):
            globals()[_k] = _v
del _m, _k, _v
