#!/usr/bin/env python3
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""mosaik.toml config honesty (Phase 5.1/5.2 of the platform-support plan).

Verifies the cartridge-geometry keys (rom_size/ram_size -> makebin bank
flags via gbdk_size_flags, including the bank(N) auto-sizing and the
too-small hard error) and the config warnings: recognised-but-unapplied
keys and unknown keys/sections warn; applied keys and metadata stay silent.
"""

from mosaik8 import BuildConfig, gbdk_size_flags


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def config_from(text):
    with tempfile.NamedTemporaryFile('w', suffix='.toml', delete=False,
                                     encoding='utf-8') as f:
        f.write(text)
        path = f.name
    try:
        return BuildConfig(path)
    finally:
        os.unlink(path)


def main():
    print("mosaik.toml config honesty + cartridge geometry")
    print("=" * 50)
    ok = True

    # --- gbdk_size_flags: geometry -> makebin flags ------------------------
    ok &= check("default cart emits no flags (byte-identical ROMs)",
                gbdk_size_flags(None, 0, 0) == [])
    ok &= check("explicit 32KB default also emits no flags",
                gbdk_size_flags(2, 0, 0) == [])
    ok &= check("banked code auto-sizes to the next power of two",
                gbdk_size_flags(None, 0, 3) == ['-Wm-yt0x19', '-Wm-yo4'])
    ok &= check("auto-size covers bank 5 with 8 banks",
                gbdk_size_flags(None, 0, 5) == ['-Wm-yt0x19', '-Wm-yo8'])
    ok &= check("bank 1 on a 32KB cart still needs the MBC",
                gbdk_size_flags(None, 0, 1) == ['-Wm-yt0x19'])
    ok &= check("cart RAM selects MBC5+RAM+BATTERY",
                gbdk_size_flags(8, 1, 0) == ['-Wm-yt0x1B', '-Wm-yo8', '-Wm-ya1'])
    ok &= check("RAM alone upgrades the default cart",
                gbdk_size_flags(None, 4, 0) == ['-Wm-yt0x1B', '-Wm-ya4'])
    try:
        gbdk_size_flags(2, 0, 3)
        ok &= check("explicit rom_size too small raises", False)
    except ValueError as e:
        ok &= check("explicit rom_size too small raises",
                    'bank 3' in str(e) and '64KB' in str(e))

    # --- BuildConfig size parsing ------------------------------------------
    config = config_from('[build]\nrom_size = "128KB"\nram_size = "32KB"\n')
    ok &= check("rom_size '128KB' -> 8 banks", config.get_rom_banks() == 8)
    ok &= check("ram_size '32KB' -> 4 banks", config.get_ram_banks() == 4)
    config = config_from('[build]\noutput_dir = "build"\n')
    ok &= check("absent sizes mean auto/none",
                config.get_rom_banks() is None and config.get_ram_banks() == 0)
    config = config_from('[build]\nrom_size = "48KB"\n')
    try:
        config.get_rom_banks()
        ok &= check("invalid rom_size raises with the valid sizes", False)
    except ValueError as e:
        ok &= check("invalid rom_size raises with the valid sizes",
                    '48KB' in str(e) and '64KB' in str(e))

    # --- config warnings ----------------------------------------------------
    config = config_from('''
[project]
name = "demo"
version = "1.0.0"
target_platforms = ["gameboy"]

[source]
folder = "src/"

[build]
output_dir = "build"
rom_size = "64KB"
optimization_level = 2

[dependencies]
stdlib = "1.0"

[platforms.gameboy]
features = ["save_support"]

[bulid]
typo = true
''')
    warnings = config.config_warnings()
    text = "\n".join(warnings)
    ok &= check("not-yet-applied build key warns",
                "'build.optimization_level' is parsed but not yet applied" in text)
    ok &= check("dependencies table warns",
                "'dependencies.stdlib' is parsed but not yet applied" in text)
    ok &= check("platform feature key warns",
                "'platforms.gameboy.features' is parsed but not yet applied" in text)
    ok &= check("unknown section warns", "unknown section '[bulid]'" in text)
    ok &= check("applied + metadata keys stay silent",
                not any(k in text for k in ('project.name', 'project.version',
                                            'source.folder', 'build.rom_size',
                                            'build.output_dir',
                                            'target_platforms')))

    config = config_from('[build]\noutput_dirr = "build"\n')
    ok &= check("misspelled key warns as unknown",
                any("unknown key 'build.output_dirr'" in w
                    for w in config.config_warnings()))

    # The in-memory defaults (no file) must never warn.
    missing = BuildConfig(os.path.join(tempfile.gettempdir(),
                                       'no-such-mosaik.toml'))
    ok &= check("defaults (no file) never warn", missing.config_warnings() == [])

    print()
    if ok:
        print("All build-config tests passed")
        return 0
    print("Some tests failed")
    return 1


if __name__ == '__main__':
    sys.exit(main())
