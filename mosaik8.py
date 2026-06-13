#!/usr/bin/env python3
"""
MosaiK8 Build Tool
"""

import argparse
import os
import sys
import subprocess
import shutil
from typing import Dict, List, Optional, Any

try:
    import toml
except ImportError:
    print("Error: toml package required. Install with: pip install toml")
    sys.exit(1)

# Import the mosaik compiler package.
try:
    from mosaik import MosaikCompiler, PLATFORM_CAPS, platform_caps
except ImportError:
    print("Error: mosaik package not found. Ensure the mosaik/ folder is in the path.")
    sys.exit(1)

# Asset pipeline (PNG -> GB 2bpp tile data, see mosaik_assets.py).
from mosaik_assets import (AssetError, load_assets, load_asset_palettes,
                           load_asset_palettes16, load_asset_sprite_defs)
from mosaik.platforms import canonical_platform

# Consoles whose sprite engine renders the native 4bpp (16-colour) tier today.
# A 4bpp-capable console NOT listed here falls back to the 2bpp grey tier
# (generalized-with-limits) until its engine gains a 4bpp path. (The PC Engine
# VDC 4bpp sprite path is a planned follow-on.)
SPRITE_4BPP_ENGINE = {'lynx'}


def target_sprite_bpp(platform: str) -> int:
    """Sprite source depth to encode assets at for this target (2 or 4)."""
    cp = canonical_platform(platform)
    if cp in SPRITE_4BPP_ENGINE:
        return platform_caps(cp).get('sprite_bpp', 2)
    return 2

# Supported target consoles. Each canonical name maps to the GBDK-2020 `lcc`
# port flags and the ROM file extension that selects the matching output
# format. See https://gbdk.org/docs/api/docs_supported_consoles.html. Keep the
# names in sync with PLATFORM_ALIASES in mosaik/platforms.py.
# `framework` selects the toolchain: 'gbdk' targets are linked by GBDK's `lcc`
# (using `flags`); 'cc65' targets are linked by cc65's `cl65` (using
# `cc65_target`, the cl65 `-t` value). Adding another cc65 console (e.g. pce,
# supervision) is one new entry here plus a 'cc65' mapping in the compiler's
# PLATFORM_FRAMEWORK — no new code path. Keep names in sync with
# PLATFORM_ALIASES / PLATFORM_FRAMEWORK in mosaik/platforms.py.
PLATFORM_TARGETS: Dict[str, Dict[str, Any]] = {
    'gameboy':         {'framework': 'gbdk', 'flags': ['-msm83:gb'],            'ext': 'gb'},
    'gameboy_color':   {'framework': 'gbdk', 'flags': ['-msm83:gb', '-Wm-yc'], 'ext': 'gbc'},
    'analogue_pocket': {'framework': 'gbdk', 'flags': ['-msm83:ap'],            'ext': 'pocket'},
    'megaduck':        {'framework': 'gbdk', 'flags': ['-msm83:duck'],          'ext': 'duck'},
    'sms':             {'framework': 'gbdk', 'flags': ['-mz80:sms'],            'ext': 'sms'},
    'gamegear':        {'framework': 'gbdk', 'flags': ['-mz80:gg'],             'ext': 'gg'},
    'nes':             {'framework': 'gbdk', 'flags': ['-mmos6502:nes'],        'ext': 'nes'},
    'lynx':            {'framework': 'cc65', 'cc65_target': 'lynx',             'ext': 'lnx'},
    # cc65's pce.cfg defaults to an 8 KB cart; link as a standard 32 KB HuCard
    # so tile-heavy programs (e.g. projects/background's 2.5 KB tileset plus
    # the bkg engine) fit, matching the GBDK consoles' 32 KB ROMs.
    'pce':             {'framework': 'cc65', 'cc65_target': 'pce',              'ext': 'pce',
                        'cc65_flags': ['-Wl', '-D__CARTSIZE__=$8000']},
}

# The build-tool target registry and the compiler's capability registry must
# describe the same set of consoles, with the same backend for each. Fail fast
# at import if a console was added to one side only.
assert set(PLATFORM_TARGETS) == set(PLATFORM_CAPS), (
    "PLATFORM_TARGETS (mosaik8.py) and PLATFORM_CAPS (mosaik/platforms.py) "
    "list different consoles: %s"
    % sorted(set(PLATFORM_TARGETS) ^ set(PLATFORM_CAPS)))
assert all(PLATFORM_TARGETS[p]['framework'] == PLATFORM_CAPS[p]['framework']
           for p in PLATFORM_TARGETS), (
    "PLATFORM_TARGETS and PLATFORM_CAPS disagree on a console's framework")


# Cartridge geometry (mosaik.toml `rom_size` / `ram_size`) in makebin units:
# ROM banks are 16 KB, cart-RAM banks 8 KB. Power-of-two ROM sizes only (the
# cartridge header encodes them that way); RAM sizes follow the header codes.
ROM_SIZE_BANKS = {'32KB': 2, '64KB': 4, '128KB': 8, '256KB': 16,
                  '512KB': 32, '1MB': 64, '2MB': 128, '4MB': 256, '8MB': 512}
RAM_SIZE_BANKS = {'0': 0, '0KB': 0, 'NONE': 0, '8KB': 1, '32KB': 4, '128KB': 16}


def gbdk_size_flags(rom_banks: Optional[int], ram_banks: int,
                    max_bank_used: int = 0) -> List[str]:
    """makebin flags (-Wm-yt/-yo/-ya) for the requested cart geometry.

    `rom_banks` is the explicit `rom_size` in 16 KB banks (None = auto-size
    to the highest `bank(N)` used, minimum the default 2). An MBC (MBC5; or
    MBC5+RAM+BATTERY when cart RAM is requested) is emitted only when the
    cart actually needs one -- banked code, more than two ROM banks, or cart
    RAM -- so plain 32 KB builds keep producing byte-identical ROMs with no
    extra flags. Raises ValueError when an explicit rom_size cannot hold
    the highest bank the program places code in (config honesty: no silent
    resizing).
    """
    needed = max_bank_used + 1
    if rom_banks is None:
        rom_banks = 2
        while rom_banks < needed:
            rom_banks *= 2
    elif rom_banks < needed:
        raise ValueError(
            "rom_size gives %d banks (16 KB each) but the program places code "
            "in bank %d; raise rom_size to at least %dKB"
            % (rom_banks, max_bank_used, needed * 16))

    flags = []
    if max_bank_used > 0 or rom_banks > 2 or ram_banks > 0:
        # MBC5: most widely emulated, 512 banks, none of MBC1's aliasing traps.
        flags.append('-Wm-yt0x1B' if ram_banks > 0 else '-Wm-yt0x19')
    if rom_banks != 2:
        flags.append('-Wm-yo%d' % rom_banks)
    if ram_banks > 0:
        flags.append('-Wm-ya%d' % ram_banks)
    return flags


def platform_framework(platform: str) -> str:
    """Return the toolchain framework ('gbdk' or 'cc65') for a target console."""
    target = PLATFORM_TARGETS.get(platform.lower(), PLATFORM_TARGETS['gameboy'])
    return target.get('framework', 'gbdk')


def platform_rom_ext(platform: str) -> str:
    """Return the ROM file extension (without dot) for a target console."""
    return PLATFORM_TARGETS.get(platform.lower(), PLATFORM_TARGETS['gameboy'])['ext']


class BuildConfig:
    """Project build configuration."""

    # mosaik.toml schema, used for the config-honesty warnings: keys the
    # build actually applies, keys that are pure metadata (silent), and keys
    # that are recognised but not yet acted on (warned). Anything else is an
    # unknown key (warned -- usually a typo). 'platforms' table entries are
    # matched per console section.
    APPLIED_KEYS = {
        'project': {'name', 'target_platforms'},
        'source': {'folder'},
        'build': {'output_dir', 'rom_size', 'ram_size'},
        'assets': {'sprites'},
    }
    METADATA_KEYS = {
        'project': {'version', 'author', 'description'},
    }
    NOT_YET_APPLIED_KEYS = {
        'build': {'optimization_level', 'debug_symbols'},
        'platforms': {'features', 'memory_layout'},
        'dependencies': None,  # whole table
    }

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or "mosaik.toml"
        self.loaded_from_file = os.path.exists(self.config_path)
        self.config = self.load_config()

    def load_config(self) -> Dict[str, Any]:
        """Load project configuration from a TOML file.

        A missing file falls back to sane defaults, but a file that exists and
        fails to parse is reported as an error rather than being silently
        ignored (which would hide the user's intended settings).
        """
        if not os.path.exists(self.config_path):
            return self.default_config()

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return toml.load(f)
        except Exception as e:
            raise RuntimeError(
                f"Failed to parse project file '{self.config_path}': {e}\n"
                f"  (TOML string values must be quoted, e.g. folder = \"src/\")")

    def default_config(self) -> Dict[str, Any]:
        """Default project configuration.

        Deliberately contains only keys the build tool acts on (plus the
        project metadata), so `init`-generated projects never trigger the
        "not yet applied" config warnings. rom_size/ram_size are omitted:
        absent means "auto" (32 KB, grown to fit `bank(N)` placements).
        """
        return {
            'project': {
                'name': 'mosaik_game',
                'version': '1.0.0',
                'target_platforms': ['gameboy', 'gameboy_color']
            },
            'source': {
                'folder': 'src/'
            },
            'build': {
                'output_dir': 'build'
            },
        }

    def get_project_name(self) -> str:
        return self.config.get('project', {}).get('name', 'mosaik_game')

    def get_target_platforms(self) -> List[str]:
        return self.config.get('project', {}).get('target_platforms', ['gameboy'])

    def get_output_dir(self) -> str:
        return self.config.get('build', {}).get('output_dir', 'build')

    def get_source_folder(self) -> str:
        return self.config.get('source', {}).get('folder', 'src')

    def get_platform_config(self, platform: str) -> Dict[str, Any]:
        return self.config.get('platforms', {}).get(platform, {})

    def get_asset_files(self) -> List[str]:
        """PNG assets to convert and link into the build ([assets] sprites)."""
        return self.config.get('assets', {}).get('sprites', [])

    def get_rom_banks(self) -> Optional[int]:
        """`[build] rom_size` in 16 KB ROM banks, or None when unset (auto).

        Raises ValueError for a size that is not a valid cartridge ROM size.
        """
        value = self.config.get('build', {}).get('rom_size')
        if value is None:
            return None
        key = str(value).strip().upper().replace(' ', '')
        if key not in ROM_SIZE_BANKS:
            raise ValueError(
                "invalid rom_size '%s' (valid: %s)"
                % (value, ", ".join(ROM_SIZE_BANKS)))
        return ROM_SIZE_BANKS[key]

    def get_ram_banks(self) -> int:
        """`[build] ram_size` in 8 KB cart-RAM banks (0 when unset).

        Raises ValueError for a size that is not a valid cart-RAM size.
        """
        value = self.config.get('build', {}).get('ram_size')
        if value is None:
            return 0
        key = str(value).strip().upper().replace(' ', '')
        if key not in RAM_SIZE_BANKS:
            raise ValueError(
                "invalid ram_size '%s' (valid: %s)"
                % (value, ", ".join(RAM_SIZE_BANKS)))
        return RAM_SIZE_BANKS[key]

    def config_warnings(self) -> List[str]:
        """Config-honesty warnings for the loaded mosaik.toml.

        Reports keys that are recognised but not yet acted on, and unknown
        keys (usually typos), so no setting is ever silently ignored. Only
        meaningful when a real file was loaded -- the in-memory defaults
        contain applied keys only.
        """
        if not self.loaded_from_file:
            return []
        warnings = []

        def check(section: str, key: str, label: str):
            ignored = self.NOT_YET_APPLIED_KEYS.get(section, set())
            if ignored is None or key in ignored:  # None = whole table
                warnings.append("'%s' is parsed but not yet applied" % label)
            elif (key in self.APPLIED_KEYS.get(section, ())
                    or key in self.METADATA_KEYS.get(section, ())):
                pass
            else:
                warnings.append("unknown key '%s' (typo?)" % label)

        known_sections = (set(self.APPLIED_KEYS) | set(self.METADATA_KEYS)
                          | set(self.NOT_YET_APPLIED_KEYS) | {'platforms'})
        for section, table in self.config.items():
            if section not in known_sections:
                warnings.append("unknown section '[%s]'" % section)
                continue
            if not isinstance(table, dict):
                warnings.append("'%s' is not a section (expected a table)"
                                % section)
                continue
            if section == 'platforms':
                # platforms.<console>.<key>: per-console sub-tables.
                for console, sub in table.items():
                    if not isinstance(sub, dict):
                        continue
                    for key in sub:
                        check('platforms', key,
                              "platforms.%s.%s" % (console, key))
                continue
            for key in table:
                check(section, key, "%s.%s" % (section, key))
        return warnings

    def report_config_warnings(self):
        """Print the config warnings once per build."""
        for message in self.config_warnings():
            print("  ⚠️ %s: %s" % (self.config_path, message))

class GBDKInterface:
    """Interface to GBDK toolchain."""

    def __init__(self):
        self.gbdk_path = self.find_gbdk()
        self.version_info = self.detect_version()

    def find_gbdk(self) -> Optional[str]:
        """Find GBDK installation with GBDK-2020 support."""
        # Priority 1: GBDK-specific environment variables
        gbdk_home = os.environ.get('GBDK_HOME')
        if gbdk_home and os.path.exists(gbdk_home):
            if self.validate_gbdk_installation(gbdk_home):
                return gbdk_home

        # Priority 2: Relative paths (common in GBDK-2020 examples)
        relative_paths = ['./gbdk-2020', '../gbdk-2020', './gbdk', '../gbdk']
        for rel_path in relative_paths:
            if os.path.exists(rel_path):
                abs_path = os.path.abspath(rel_path)
                if self.validate_gbdk_installation(abs_path):
                    return abs_path

        # Priority 3: Standard installation paths
        common_paths = [
            '/usr/local/gbdk-2020', '/usr/local/gbdk',
            '/opt/gbdk-2020', '/opt/gbdk',
            os.path.expanduser('~/gbdk-2020'), os.path.expanduser('~/gbdk'),
            'C:\\gbdk-2020', 'C:\\gbdk',
            'C:\\Program Files\\gbdk-2020', 'C:\\Program Files\\gbdk'
        ]

        for path in common_paths:
            if os.path.exists(path) and self.validate_gbdk_installation(path):
                return path

        # Priority 4: Tools in PATH
        if shutil.which('lcc'):
            lcc_path = shutil.which('lcc')
            potential_gbdk = os.path.dirname(os.path.dirname(lcc_path))
            if self.validate_gbdk_installation(potential_gbdk):
                return potential_gbdk

        return None

    def validate_gbdk_installation(self, path: str) -> bool:
        """Validate GBDK installation."""
        required_dirs = ['bin', 'lib', 'include']
        required_tools = ['lcc', 'sdcc', 'sdasgb', 'makebin']

        # Check directories
        if not all(os.path.exists(os.path.join(path, d)) for d in required_dirs):
            return False

        # Check tools
        bin_dir = os.path.join(path, 'bin')
        for tool in required_tools:
            if not self._find_tool_in_dir(tool, bin_dir):
                return False

        return True

    def _find_tool_in_dir(self, tool: str, bin_dir: str) -> bool:
        """Check if tool exists in directory."""
        variants = [tool, f"{tool}.exe"] if os.name == 'nt' else [tool]
        return any(os.path.exists(os.path.join(bin_dir, variant)) for variant in variants)

    def detect_version(self) -> Dict[str, str]:
        """Report the GBDK toolchain type (GBDK-2020 is the only one supported)."""
        try:
            self.get_tool_path('lcc')
            return {'type': 'GBDK-2020'}
        except Exception:
            return {'type': 'Unknown'}

    def get_tool_path(self, tool: str) -> str:
        """Get path to GBDK tool."""
        # Try GBDK installation first
        if self.gbdk_path:
            bin_dir = os.path.join(self.gbdk_path, 'bin')
            variants = [tool, f"{tool}.exe"] if os.name == 'nt' else [tool]

            for variant in variants:
                tool_path = os.path.join(bin_dir, variant)
                if os.path.exists(tool_path):
                    return tool_path

        # Fallback to PATH
        variants = [tool, f"{tool}.exe"] if os.name == 'nt' else [tool]
        for variant in variants:
            tool_in_path = shutil.which(variant)
            if tool_in_path:
                return tool_in_path

        raise FileNotFoundError(
            f"GBDK tool '{tool}' not found. "
            f"Ensure GBDK is installed and GBDK_HOME is set or tools are in PATH."
        )

    def get_platform_flags(self, platform: str) -> List[str]:
        """Get platform-specific flags for GBDK-2020.

        GBDK-2020 platform flags. Game Boy Color ROMs use the standard
        sm83:gb port with the makebin CGB-compatibility flag (-Wm-yc) rather
        than a separate port. All other consoles select their own port.
        """
        target = PLATFORM_TARGETS.get(platform.lower())
        return list(target['flags']) if target else ['-msm83:gb']

    def compile_assembly(self, c_files: List[str], output_file: str, platform: str,
                        debug: bool = False,
                        extra_flags: Optional[List[str]] = None) -> bool:
        """Compile the generated C file(s) to a ROM via GBDK's `lcc`.

        Banked builds pass several C files (the home TU plus one TU per ROM
        bank -- SDCC's `#pragma bank` is file-scoped, so each bank needs its
        own translation unit); `lcc` compiles each and links them together.
        `extra_flags` carries the cartridge-geometry makebin flags
        (-Wm-yt/-yo/-ya) when the cart needs an MBC.
        """
        try:
            lcc = self.get_tool_path('lcc')
            flags = []

            # Target platform (e.g. -msm83:gb for Game Boy)
            flags.extend(self.get_platform_flags(platform))
            flags.extend(extra_flags or [])

            if debug:
                flags.extend(['-debug', '-Wa-l', '-Wl-m'])

            # GBDK include path
            if self.gbdk_path:
                include_path = os.path.join(self.gbdk_path, 'include')
                flags.extend([f'-I{include_path}'])

            # Build command. `lcc` compiles the generated GBDK C straight to ROM.
            cmd = [lcc, *flags, '-o', output_file, *c_files]

            print(f"Compiling with {self.version_info.get('type', 'GBDK')}: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                print(f"Compilation failed:")
                print("STDOUT:", result.stdout)
                print("STDERR:", result.stderr)

                # Provide version-specific hints
                self._provide_error_hints(result.stderr)
                return False

            return True

        except Exception as e:
            print(f"Error running GBDK tools: {e}")
            return False

    def _provide_error_hints(self, stderr: str):
        """Provide helpful error hints for a failed GBDK build."""
        hints = []

        if "not found" in stderr.lower():
            hints.append("• Check GBDK installation path")
            hints.append("• Verify GBDK_HOME environment variable")

        if "bank" in stderr.lower():
            hints.append("• Try using -autobank flag for automatic banking")

        if hints:
            print("\nHints:")
            for hint in hints:
                print(hint)


class Cc65Interface:
    """Interface to the cc65 toolchain (used for non-GBDK consoles, e.g. Lynx).

    cc65's `cl65` driver compiles + assembles + links C straight to a cartridge
    image in one step, so there is no separate assemble/link split as with
    GBDK's `lcc`. `cl65` locates its own cfg/lib/include relative to its binary,
    so a bundled `cc65/` tree needs no extra include flags.
    """

    def __init__(self):
        self.cc65_path = self.find_cc65()

    def find_cc65(self) -> Optional[str]:
        """Find a cc65 installation (the directory containing bin/cl65)."""
        # Priority 1: CC65_HOME environment variable.
        cc65_home = os.environ.get('CC65_HOME')
        if cc65_home and self.validate_cc65_installation(cc65_home):
            return cc65_home

        # Priority 2: a bundled cc65/ next to the tool (as shipped here).
        here = os.path.dirname(os.path.abspath(__file__))
        for rel in ('cc65', '../cc65', 'cc65-snapshot'):
            cand = os.path.abspath(os.path.join(here, rel))
            if self.validate_cc65_installation(cand):
                return cand

        # Priority 3: cl65 on PATH.
        cl65 = shutil.which('cl65')
        if cl65:
            cand = os.path.dirname(os.path.dirname(cl65))
            if self.validate_cc65_installation(cand):
                return cand
        return None

    def validate_cc65_installation(self, path: str) -> bool:
        """A cc65 tree must have bin/cl65 plus cfg/ and lib/ directories."""
        if not path or not os.path.isdir(path):
            return False
        if not all(os.path.isdir(os.path.join(path, d)) for d in ('cfg', 'lib')):
            return False
        return self._find_cl65(path) is not None

    def _find_cl65(self, path: str) -> Optional[str]:
        bin_dir = os.path.join(path, 'bin')
        for name in ('cl65', 'cl65.exe'):
            cand = os.path.join(bin_dir, name)
            if os.path.isfile(cand):
                return cand
        return None

    def is_available(self) -> bool:
        return self.cc65_path is not None

    def link_target(self, c_files: List[str], output_file: str, cc65_target: str,
                    debug: bool = False,
                    extra_flags: Optional[List[str]] = None) -> bool:
        """Compile and link the given C files into a cartridge image via cl65."""
        if not c_files:
            return False
        if not self.is_available():
            print("Error: cc65 toolchain not found.")
            print("  Set CC65_HOME, bundle a cc65/ folder next to mosaik8.py, "
                  "or put cl65 on PATH.")
            return False

        cl65 = self._find_cl65(self.cc65_path)
        flags = ['-t', cc65_target, '-O', *(extra_flags or [])]
        if debug:
            flags.extend(['-g', '-Ln', output_file + '.lbl'])

        cmd = [cl65, *flags, '-o', output_file, *c_files]
        print(f"Compiling with cc65: {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except Exception as e:
            print(f"Error running cc65 tools: {e}")
            return False

        if result.returncode != 0:
            print("Compilation failed:")
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)
            return False
        return True


class SourceManager:
    """Manages mosaik source files and dependencies."""

    def __init__(self, config: BuildConfig):
        self.config = config
        self.source_files = []

    def find_source_files(self, search_paths: List[str] = None) -> List[str]:
        """Find all mosaik source files."""
        if search_paths is None:
            search_paths = ['.', 'src']

        source_files = []
        seen_files = set()

        for search_path in search_paths:
            if os.path.isfile(search_path):
                if search_path.endswith('.mos'):
                    abs_path = os.path.abspath(search_path)
                    if abs_path not in seen_files:
                        source_files.append(search_path)
                        seen_files.add(abs_path)
            elif os.path.isdir(search_path):
                for root, dirs, files in os.walk(search_path):
                    # Never descend into build output directories.
                    dirs[:] = [d for d in dirs if d != 'build']
                    for file in files:
                        if file.endswith('.mos'):
                            file_path = os.path.join(root, file)
                            abs_path = os.path.abspath(file_path)
                            if abs_path not in seen_files:
                                source_files.append(file_path)
                                seen_files.add(abs_path)

        return source_files

    def extract_imports(self, source_file: str) -> List[str]:
        """Extract import statements from source file."""
        imports = []
        try:
            with open(source_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Simple regex-based import extraction
            import re
            pattern = r'import\s+"([^"]+)"'
            matches = re.findall(pattern, content)
            imports.extend(matches)

        except Exception as e:
            print(f"Warning: Could not analyze imports in {source_file}: {e}")

        return imports

class MosaikBuilder:
    """Main mosaik build system."""

    def __init__(self, config_path: Optional[str] = None):
        self.config = BuildConfig(config_path)
        self.gbdk = GBDKInterface()
        self.cc65 = Cc65Interface()
        self.source_manager = SourceManager(self.config)
        self.compiler = MosaikCompiler()

    def build(self, target: str = None, platform: str = None, debug: bool = False,
              asset_files: List[str] = None) -> bool:
        """Build mosaik sources.

        Exactly two modes are supported, selected by the `target` argument:

        * Single-file mode -- `target` is a `.mos` file.  A `build/` folder is
          created next to that file and the generated `.c` and `.gb`/`.gbc`
          ROM are named after the source file.

        * Project mode -- `target` is a `mosaik.toml` (or a directory
          containing one, or omitted to use ./mosaik.toml).  The project's
          `[source] folder` is compiled, output goes to the project's
          `[build] output_dir`, and the ROM is named after `[project] name`.

        There is deliberately no "scan everything" mode.
        """
        print("MosaiK8 Build Tool v1.0.0")
        print("=" * 40)

        # Single-file mode
        if target and target.endswith('.mos'):
            return self.build_single_file(target, platform, debug, asset_files)

        # Project mode -- resolve the project file.
        project_file = self._resolve_project_file(target)
        if project_file is None:
            return False
        return self.build_project(project_file, platform, debug, asset_files)

    def _resolve_project_file(self, target: str) -> Optional[str]:
        """Locate the mosaik.toml to use for project mode."""
        if target is None:
            candidate = 'mosaik.toml'
        elif os.path.isdir(target):
            candidate = os.path.join(target, 'mosaik.toml')
        else:
            candidate = target  # assume a path to a .toml file

        if not os.path.isfile(candidate):
            print(f"Error: project file not found: {candidate}")
            print("Specify a .mos source file, or a mosaik.toml project file.")
            return None
        return candidate

    def build_single_file(self, source_file: str, platform: str, debug: bool,
                          asset_files: List[str] = None) -> bool:
        """Build a standalone `.mos` file, placing build/ next to the source."""
        if not os.path.isfile(source_file):
            print(f"Error: source file not found: {source_file}")
            return False

        base_name = os.path.splitext(os.path.basename(source_file))[0]
        source_dir = os.path.dirname(os.path.abspath(source_file))
        output_dir = os.path.join(source_dir, 'build')

        print(f"Building file: {source_file}")
        print(f"  Output: {output_dir}")

        # Cross-file module linking: pull in the .mos files for any
        # non-stdlib imports (resolved next to the importing file).
        source_files = self._resolve_import_closure(source_file)
        if source_files is None:
            return False
        # (no os.path.relpath here -- it raises for paths on another drive)
        for extra in source_files[1:]:
            print(f"  Linked source: {extra}")
        print()

        # A single file has no project config; default to both platforms.
        # (A ./mosaik.toml in the working directory still supplies defaults --
        # report its ignored/unknown keys like project mode does.)
        self.config.report_config_warnings()
        target_platforms = [platform] if platform else self.config.get_target_platforms()

        # PNG assets come from --asset flags in single-file mode (paths are
        # relative to the working directory). Conversion is per-target: the
        # sprite depth (2bpp / 4bpp) depends on the console (see _convert_assets).
        success = True
        for target_platform in target_platforms:
            print(f"Building for platform: {target_platform}")
            converted = self._convert_assets(asset_files or [],
                                              target_sprite_bpp(target_platform))
            if converted is None:
                success = False
                continue
            assets, asset_palettes, asset_palettes16, asset_sprites = converted
            if not self.build_target(source_files, target_platform, output_dir,
                                     base_name, debug, assets, asset_palettes,
                                     asset_palettes16, asset_sprites):
                success = False
        return success

    def _resolve_import_closure(self, entry_file: str) -> Optional[List[str]]:
        """Resolve a single file's non-stdlib imports to sibling .mos files.

        `import "name"` either names a stdlib module (resolved by the
        compiler) or another source file, located relative to the importing
        file's folder: `name.mos`, with dots mapping to subfolders
        (`import "game.utils"` -> `game/utils.mos`, falling back to
        `game.utils.mos`). Follows imports transitively. Returns the source
        list (entry first) or None when an import cannot be found.
        """
        from mosaik import stdlib_module_names
        stdlib_names = stdlib_module_names()

        entry = os.path.abspath(entry_file)
        ordered = [entry]
        seen = {entry}
        queue = [entry]
        while queue:
            current = queue.pop(0)
            base_dir = os.path.dirname(current)
            for name in self.source_manager.extract_imports(current):
                if name in stdlib_names:
                    continue
                candidates = []
                for candidate in (os.path.join(base_dir, *name.split('.')) + '.mos',
                                  os.path.join(base_dir, name + '.mos')):
                    if candidate not in candidates:
                        candidates.append(candidate)
                path = next((os.path.abspath(c) for c in candidates
                             if os.path.isfile(c)), None)
                if path is None:
                    print(f"Error: import \"{name}\" in {current} not found")
                    print("  (looked for " + " and ".join(candidates)
                          + "; stdlib modules are: "
                          + ", ".join(sorted(stdlib_names)) + ")")
                    return None
                if path not in seen:
                    seen.add(path)
                    ordered.append(path)
                    queue.append(path)
        return ordered

    def _convert_assets(self, asset_paths: List[str], sprite_bpp: int = 2):
        """Convert PNG assets to tile data for a target of depth `sprite_bpp`.

        2bpp targets get the universal GB 2bpp encoding (the historical
        default); a 4bpp target (Lynx) with a >4-colour indexed PNG gets the
        native packed-nibble 4bpp encoding plus the asset's 16-colour palette
        (`<name>_palette16`). Indexed PNGs with <=4 entries also carry their
        authored 4-colour palette (`<name>_palette`). Returns
        ([(name, data, bpp)], [(name, colors4)], [(name, colors16)]) or None.
        """
        if not asset_paths:
            return [], [], [], []
        try:
            assets = load_assets(asset_paths, sprite_bpp)
            palettes = load_asset_palettes(asset_paths)
            palettes16 = load_asset_palettes16(asset_paths)
            sprite_defs = load_asset_sprite_defs(asset_paths)
        except AssetError as e:
            print(f"Error: {e}")
            return None
        with_palette = {name for name, _ in palettes}
        for name, data, bpp in assets:
            tile_size = 32 if bpp == 4 else 16
            fmt = "4bpp" if bpp == 4 else "GB 2bpp"
            extra = ", authored palette" if name in with_palette else ""
            print(f"  Asset: {name} ({len(data) // tile_size} tiles, {fmt}{extra})")
        for sname, _off, wt, ht in sprite_defs:
            print(f"    Sprite: {sname} ({wt}x{ht} tiles)")
        return assets, palettes, palettes16, sprite_defs

    def build_project(self, project_file: str, platform: str, debug: bool,
                      asset_files: List[str] = None) -> bool:
        """Build a project described by a mosaik.toml file."""
        try:
            self.config = BuildConfig(project_file)
        except RuntimeError as e:
            print(f"Error: {e}")
            return False

        project_dir = os.path.dirname(os.path.abspath(project_file))
        source_folder = os.path.join(project_dir, self.config.get_source_folder())
        output_dir = os.path.join(project_dir, self.config.get_output_dir())
        rom_name = self.config.get_project_name()

        print(f"Project: {rom_name}  ({project_file})")
        print(f"  Sources: {source_folder}")
        print(f"  Output:  {output_dir}")
        self.config.report_config_warnings()

        if not os.path.isdir(source_folder):
            print(f"Error: source folder not found: {source_folder}")
            return False

        source_files = self.source_manager.find_source_files([source_folder])
        if not source_files:
            print(f"Error: no .mos source files found in {source_folder}")
            return False

        print(f"  Found {len(source_files)} source file(s):")
        for sf in source_files:
            print(f"    • {sf}")

        # PNG assets: the project's `[assets] sprites` list (paths relative to
        # the project file), plus any extra --asset flags (relative to cwd).
        asset_paths = [os.path.join(project_dir, p)
                       for p in self.config.get_asset_files()]
        asset_paths.extend(asset_files or [])
        print()

        target_platforms = [platform] if platform else self.config.get_target_platforms()

        # Per-target asset conversion (sprite depth varies by console).
        success = True
        for target_platform in target_platforms:
            print(f"Building for platform: {target_platform}")
            converted = self._convert_assets(asset_paths,
                                             target_sprite_bpp(target_platform))
            if converted is None:
                success = False
                continue
            assets, asset_palettes, asset_palettes16, asset_sprites = converted
            if not self.build_target(source_files, target_platform, output_dir,
                                     rom_name, debug, assets, asset_palettes,
                                     asset_palettes16, asset_sprites):
                success = False
        return success

    def build_target(self, source_files: List[str], platform: str, output_dir: str,
                     rom_name: str, debug: bool, assets: list = None,
                     asset_palettes: list = None, asset_palettes16: list = None,
                     asset_sprites: list = None) -> bool:
        """Compile the given sources and link a ROM for one platform."""
        platform_dir = os.path.join(output_dir, platform)
        os.makedirs(platform_dir, exist_ok=True)

        # Report the toolchain for this console (selected by its framework).
        if platform_framework(platform) == 'cc65':
            print(f"  Using cc65 for {platform}")
        else:
            print(f"  Using GBDK-2020 for {platform}")

        # Set platform (also selects the codegen backend, gbdk vs cc65)
        self.compiler.code_generator.platform = platform

        # Compile all sources together into one C translation unit named after
        # the output (whole-program compilation: cross-module references link
        # at the C level inside the single TU). Programs that place code in
        # ROM banks via `bank(N)` get one extra TU per bank (SDCC's
        # `#pragma bank` is file-scoped).
        c_files = self.compile_sources(source_files, platform_dir, platform,
                                       rom_name, assets, asset_palettes,
                                       asset_palettes16, asset_sprites)
        if not c_files:
            return False

        # Link into ROM (extension selects the console's output format)
        rom_file = os.path.join(platform_dir, f"{rom_name}.{platform_rom_ext(platform)}")
        return self.link_rom(c_files, rom_file, platform, debug)

    def compile_sources(self, source_files: List[str], output_dir: str,
                        platform: str, out_name: str, assets: list = None,
                        asset_palettes: list = None,
                        asset_palettes16: list = None,
                        asset_sprites: list = None) -> Optional[List[str]]:
        """Compile mosaik sources to C (GBDK or cc65 backend).

        Returns the list of generated C files: the main translation unit,
        plus one `<name>_bank<N>.c` per ROM bank the program places code in
        with `bank(N)` (GB-family consoles only; empty everywhere else).
        """
        try:
            sources = []
            for source_file in source_files:
                print(f"  Compiling {source_file}...")
                with open(source_file, 'r', encoding='utf-8') as f:
                    source_code = f.read()
                # Add platform-specific compilation directives
                sources.append((source_file,
                                self.add_platform_directives(source_code, platform)))

            # Compile to C for this target console (drives `if platform ==`
            # conditional compilation and the platform-specific prelude).
            c_code = self.compiler.compile_program(sources, platform=platform,
                                                   assets=assets,
                                                   asset_palettes=asset_palettes,
                                                   asset_palettes16=asset_palettes16,
                                                   asset_sprites=asset_sprites)

            if c_code.startswith("Compilation error:"):
                print(f"    ❌ {c_code}")
                return None

            c_file = os.path.join(output_dir, f"{out_name}.c")
            with open(c_file, 'w', encoding='utf-8') as f:
                f.write(c_code)

            print(f"    ✅ Generated {c_file}")
            c_files = [c_file]

            # Bank TUs (bank(N) placements; see the banking design doc).
            for bank, bank_code in sorted(
                    self.compiler.code_generator.bank_units.items()):
                bank_file = os.path.join(output_dir,
                                         f"{out_name}_bank{bank}.c")
                with open(bank_file, 'w', encoding='utf-8') as f:
                    f.write(bank_code)
                print(f"    ✅ Generated {bank_file} (ROM bank {bank})")
                c_files.append(bank_file)
            return c_files

        except Exception as e:
            print(f"    ❌ Error: {e}")
            return None

    def add_platform_directives(self, source_code: str, platform: str) -> str:
        """Add platform-specific compilation directives."""
        platform_config = self.config.get_platform_config(platform)

        # Add platform directive at the top
        directives = [f"# platform {platform}"]

        # Add feature flags
        features = platform_config.get('features', [])
        for feature in features:
            directives.append(f"# feature {feature}")

        directive_block = "\n".join(directives) + "\n\n"
        return directive_block + source_code

    def link_rom(self, c_files: List[str], rom_file: str, platform: str, debug: bool = False) -> bool:
        """Link the generated C into a cartridge image for the target console.

        Dispatches on the target's framework: cc65 consoles go through `cl65`
        (all C files at once); GBDK consoles go through `lcc` (single TU).
        """
        if not c_files:
            return False

        print(f"  Linking ROM: {rom_file}")

        if platform_framework(platform) == 'cc65':
            target = PLATFORM_TARGETS[platform.lower()]
            cc65_target = target['cc65_target']
            success = self.cc65.link_target(c_files, rom_file, cc65_target, debug,
                                            target.get('cc65_flags'))
            if success and cc65_target == 'pce':
                self._fixup_pce_image(rom_file)
            if success:
                file_size = os.path.getsize(rom_file)
                print(f"    ✅ ROM created: {rom_file} ({file_size} bytes)")
                print(f"    📊 Compiled with cc65 (target {cc65_target})")
            return success

        # GBDK path: `lcc` compiles the generated C (home TU + any bank TUs)
        # straight to ROM, with the cartridge-geometry flags when needed.
        try:
            extra_flags = self._gbdk_cart_flags(platform)
        except ValueError as e:
            print(f"    ❌ Error: {e}")
            return False
        success = self.gbdk.compile_assembly(c_files, rom_file, platform, debug,
                                             extra_flags)

        if success:
            file_size = os.path.getsize(rom_file)
            print(f"    ✅ ROM created: {rom_file} ({file_size} bytes)")
            print(f"    📊 Compiled with {self.gbdk.version_info.get('type', 'GBDK-2020')}")

        return success

    def _gbdk_cart_flags(self, platform: str) -> List[str]:
        """Cartridge-geometry makebin flags for a GBDK console.

        rom_size/ram_size (and `bank(N)` placements, via the code generator's
        bank_units) translate to MBC5 + bank-count flags on consoles with
        banked-ROM support (PLATFORM_CAPS has_banking: the GB family minus
        the Mega Duck, whose cart mapper is unverified). On the other GBDK
        consoles a non-default rom_size/ram_size is reported as not applied
        rather than silently ignored. Raises ValueError for invalid sizes or
        an explicit rom_size too small for the banks the program uses.
        """
        max_bank = max(self.compiler.code_generator.bank_units, default=0)
        rom_banks = self.config.get_rom_banks()
        ram_banks = self.config.get_ram_banks()
        if platform_caps(platform)['has_banking']:
            return gbdk_size_flags(rom_banks, ram_banks, max_bank)
        if rom_banks not in (None, 2) or ram_banks:
            print("  ⚠️ rom_size/ram_size are not applied on '%s' "
                  "(banked carts are only wired up for the Game Boy family)"
                  % platform)
        return []

    @staticmethod
    def _fixup_pce_image(rom_file: str):
        """Turn cc65's linker output into a bootable HuCard image.

        cc65's pce.cfg lays the ROM out linearly for the CPU ($8000-$FFFF for
        a 32 KB cart), which puts the boot bank -- startup code + the 6502
        vectors at $FFF6 -- at the *end* of the file. But a HuCard maps file
        offset 0 to physical bank 0, the bank the console sees at $E000-$FFFF
        on reset (MPR7 = 0). So for carts larger than one 8 KB bank, the last
        bank must be moved to the front (the cc65 PCE docs describe exactly
        this dd-style rotation when "creating a cartridge image"). 8 KB
        images are a single bank and already correct.
        """
        with open(rom_file, 'rb') as f:
            data = f.read()
        if len(data) <= 0x2000:
            return
        with open(rom_file, 'wb') as f:
            f.write(data[-0x2000:])
            f.write(data[:-0x2000])

    def clean(self) -> bool:
        """Clean build artifacts."""
        output_dir = self.config.get_output_dir()
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
            print(f"Cleaned build directory: {output_dir}")
        return True

    def init_project(self, project_name: str = None) -> bool:
        """Initialize a new mosaik project."""
        if project_name:
            os.makedirs(project_name, exist_ok=True)
            os.chdir(project_name)

        # Create project structure
        os.makedirs('src', exist_ok=True)

        # Create mosaik.toml
        config = self.config.default_config()
        if project_name:
            config['project']['name'] = project_name

        with open('mosaik.toml', 'w', encoding='utf-8') as f:
            toml.dump(config, f)

        # Create sample main.gb
        sample_code = '''module "main" {
    import "platform.video"
    import "platform.input"

    var frame_count: u8 = 0

    function main() {
        video.enable_lcd()

        loop {
            frame_count += 1
            video.wait_vblank()
        }
    }

    export main
}'''

        with open('src/main.mos', 'w', encoding='utf-8') as f:
            f.write(sample_code)

        print(f"Initialized mosaik project: {project_name or '.'}")
        print("Files created:")
        print("  • mosaik.toml")
        print("  • src/main.mos")
        print("\nNext steps:")
        print("  python mosaik8.py build")
        print("  # then open the built ROM in an emulator (e.g. pyboy build/gameboy/<name>.gb)")

        return True

def main():
    """Main entry point."""
    # Ensure console output (which uses status emoji) never crashes on
    # consoles with a non-UTF-8 default encoding such as Windows cp1252.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description='MosaiK8 Build Tool')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Build command
    build_parser = subparsers.add_parser('build', help='Build a mosaik file or project')
    build_parser.add_argument('--platform', choices=list(PLATFORM_TARGETS.keys()),
                             help='Target console (overrides the project setting)')
    build_parser.add_argument('--debug', action='store_true',
                             help='Generate debug symbols')
    build_parser.add_argument('--asset', action='append', default=[],
                             metavar='PNG', dest='assets',
                             help='PNG to convert to tile data and link in '
                                  '(repeatable; projects can also list assets '
                                  'in mosaik.toml under [assets] sprites)')
    build_parser.add_argument('target', nargs='?',
                             help='A .mos source file, or a mosaik.toml '
                                  '(or a directory containing one; defaults to '
                                  './mosaik.toml)')

    # Clean command
    subparsers.add_parser('clean', help='Clean build artifacts')

    # Init command
    init_parser = subparsers.add_parser('init', help='Initialize new project')
    init_parser.add_argument('name', nargs='?', help='Project name')

    # Version command
    subparsers.add_parser('version', help='Show version')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Handle version
    if args.command == 'version':
        print("MosaiK8 Build Tool v1.0.0")
        return 0

    # Create builder
    builder = MosaikBuilder()
    if builder.gbdk.gbdk_path:
        version_type = builder.gbdk.version_info.get('type', 'Unknown')
        print(f"Detected: {version_type} at {builder.gbdk.gbdk_path}")
        print("✅ GBDK-2020 features enabled")
    else:
        print("❌ GBDK not found")

    # Execute command
    try:
        if args.command == 'build':
            success = builder.build(args.target, args.platform, args.debug,
                                    args.assets)
            return 0 if success else 1

        elif args.command == 'clean':
            success = builder.clean()
            return 0 if success else 1

        elif args.command == 'init':
            success = builder.init_project(args.name)
            return 0 if success else 1

        else:
            parser.print_help()
            return 1

    except KeyboardInterrupt:
        print("\nBuild interrupted.")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1

if __name__ == '__main__':
    sys.exit(main())
