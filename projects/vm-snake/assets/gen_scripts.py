#!/usr/bin/env python3
"""Assemble scripts/snake.v8s -> src/scripts.mos (headless, reproducible).

The game is authored as VM8 text assembly, so this is the project's whole
build step: mosaik_vm.asm reads the .v8s and CompiledProgram emits the same
scripts.mos an .evt.toml event list would. (In the studio, the playground's
Regenerate button does exactly this.)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(ROOT)))

from mosaik_vm.asm import assemble_v8s_path  # noqa: E402

prog = assemble_v8s_path(os.path.join(ROOT, "scripts", "snake.v8s"))
out = os.path.join(ROOT, "src", "scripts.mos")
with open(out, "w", encoding="utf-8") as f:
    f.write(prog.to_scripts_mos())
print("wrote %s (%d bytes of bytecode, %d entries)"
      % (out, len(prog.code), len(prog.offsets)))
