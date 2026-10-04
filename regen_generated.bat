@echo off
REM Regenerate EVERY generated module of every project (nine of them, four
REM different generators) - scripts.mos is only one.
REM   regen_generated.bat            regenerate what is stale
REM   regen_generated.bat --check    report only, write nothing
REM   regen_generated.bat --only rooms,scenes
REM scenes.mos and rooms.mos are a COUPLED pair - regenerating one alone
REM stops the project compiling (measured 2026-09-08 on seven samples).
python "%~dp0tools\regen_generated.py" %*
