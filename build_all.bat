@echo off
REM ===========================================================================
REM build_all.bat - compile every MosaiK8 project and every sample.
REM
REM   * Projects build with their own mosaik.toml target_platforms.
REM   * Samples build once per console (all nine in PLATFORM_TARGETS).
REM
REM Output ROMs land in each source's build/ dir (samples -> samples/build/).
REM Run from the mosaik8/ repo root:  build_all.bat
REM ===========================================================================
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "PY=python"
set "PASS=0"
set "FAIL=0"
set "FAILED="

REM --- The nine consoles (must match PLATFORM_TARGETS in mosaik8.py) ----------
set "CONSOLES=gameboy gameboy_color analogue_pocket megaduck sms gamegear nes lynx pce"

echo ===========================================================================
echo  Building all PROJECTS (each uses its mosaik.toml target_platforms)
echo ===========================================================================
for /d %%P in (projects\*) do (
    if exist "%%P\mosaik.toml" (
        echo.
        echo --- build %%P ---
        %PY% mosaik8.py build "%%P"
        if errorlevel 1 (
            set /a FAIL+=1
            set "FAILED=!FAILED! %%P"
        ) else (
            set /a PASS+=1
        )
    )
)

echo.
echo ===========================================================================
echo  Building all SAMPLES x every console
echo ===========================================================================
for %%S in (samples\*.mos) do (
    for %%C in (%CONSOLES%) do (
        echo.
        echo --- build %%S [%%C] ---
        %PY% mosaik8.py build --platform %%C "%%S"
        if errorlevel 1 (
            set /a FAIL+=1
            set "FAILED=!FAILED! %%~nS:%%C"
        ) else (
            set /a PASS+=1
        )
    )
)

echo.
echo ===========================================================================
echo  Summary:  !PASS! ok, !FAIL! failed
echo ===========================================================================
if !FAIL! gtr 0 (
    echo Failed builds:
    for %%F in (!FAILED!) do echo   - %%F
    exit /b 1
)
echo All builds succeeded.
exit /b 0
