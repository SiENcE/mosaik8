"""MosaiK8 build pipeline: BuildConfig + the GBDK/cc65 interfaces + MosaikBuilder
(split out of mosaik8.py)."""
import os
import re
import sys
import subprocess
import shutil
from typing import Dict, List, Optional, Any

try:
    import toml
except ImportError:
    print("Error: toml package required. Install with: pip install toml")
    sys.exit(1)

from mosaik import MosaikCompiler, PLATFORM_CAPS, platform_caps
from mosaik_assets import (AssetError, load_assets, load_asset_palettes,
                           load_asset_palettes16, load_asset_sprite_defs)
from mosaik.platforms import canonical_platform, obj16_effective
from mosaik8_targets import *  # noqa: F401,F403


def tool_prefix() -> Optional[str]:
    """Where `setup_tools.py` put the fetched toolchains, if not beside us.

    `$MOSAIK8_HOME` is the one variable both sides read: an INSTALLED studio
    fetches GBDK/cc65/cores into a per-user directory because its own tree is
    read-only, and the build has to look there. Unset (the checkout case) this
    returns None and every finder behaves exactly as it did before."""
    home = os.environ.get('MOSAIK8_HOME')
    if not home:
        return None
    home = os.path.abspath(os.path.expanduser(home))
    return home if os.path.isdir(home) else None


def _short_path(path: str) -> Optional[str]:
    """The Windows 8.3 form of `path`, or None where there is none.

    A path that does not exist yet (the ROM `lcc` is about to write) keeps its
    missing tail as written and shortens the deepest ancestor that does exist.
    None off Windows, and on a volume with 8.3 names disabled the API answers
    the long form, which the caller sees as a path still carrying a space."""
    if os.name != 'nt':
        return None
    import ctypes
    head, tail = os.path.abspath(path), []
    while head and not os.path.exists(head):
        head, leaf = os.path.split(head)
        if not leaf:
            return None
        tail.insert(0, leaf)
    buf = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetShortPathNameW(head, buf, len(buf)):
        return None
    return os.path.join(buf.value, *tail)


#: The spaceless stem lcc links under when a FILE NAME has a space (a project
#: called "My Game" names its ROM and its C files after itself). The ROM links
#: as `mk8_link.<ext>` and is renamed back; a spaced input links from a
#: `mk8_link_<name>` copy that is deleted after. See `gbdk_spaceless_command`.
GBDK_LINK_STANDIN = 'mk8_link'


def gbdk_spaceless_command(cmd: List[str], cwd: str):
    """Rewrite an `lcc` argv so no PATH in it carries a space.

    Returns ``(argv, run_in, standin)``: the argv, the directory to run lcc in
    (None = where it always ran), and None or the stand-in plan
    ``{'rom': (stand-in ROM, real ROM) or None, 'copies': [(real, copy)]}``
    that `begin_gbdk_standin` / `finish_gbdk_standin` carry out around the
    link.

    **GBDK's lcc splices its paths into the sdcc / sdas / sdld command lines it
    builds WITHOUT QUOTING** (measured on GBDK-2020 4.x, Windows): a source at
    `...\\MosaiK8 Projects\\x\\build\\gameboy\\x.c` reaches sdcc as two
    arguments, "don't know what to do with file '...\\MosaiK8'". The installed
    studio's default project folder is `Documents\\MosaiK8 Projects`, so every
    Game Boy build of a new project failed. cc65's cl65 quotes correctly and
    needs none of this.

    Each path argument (the tool, `-I<dir>`, the `-o` target, the inputs) is
    unchanged without a space, so every existing command line is byte-for-byte
    what it was. Otherwise:

    * a FOLDER becomes its 8.3 short form, else (inside `cwd`, the build
      directory) a relative name, and lcc runs IN `cwd`;
    * a FILE NAME is never 8.3-shortened. lcc names the `.map` (and a `-Wl-j`
      `.noi`) after `-o`; sdcc names a module after its file, `~` in it breaks
      `-debug`'s assembler symbols, and an 8.3 `X.C` is not compiled as C (all
      measured). So a spaced ROM name links under the stand-in and is renamed
      back, and a spaced input links from a spaceless COPY. The input names do
      not reach the ROM: the banked `vm-showcase` linked from renamed sources
      is md5-identical.

    What still carries a space raises ValueError naming it, before lcc
    garbles it."""
    out, run_in, bad = [], None, []
    plan = {'rom': None, 'copies': []}
    flag_before = None

    def fix(path, kind):
        nonlocal run_in
        if ' ' not in path:
            return path
        if kind == 'dir':
            folder, leaf = path, ''
        else:
            folder, leaf = os.path.split(path)
        if ' ' in leaf and kind in ('out', 'in'):
            real = os.path.abspath(path)
            if kind == 'out':
                leaf = GBDK_LINK_STANDIN + os.path.splitext(leaf)[1]
            else:
                leaf = GBDK_LINK_STANDIN + '_' + leaf.replace(' ', '_')
            stand = os.path.join(os.path.dirname(real), leaf)
            if kind == 'out':
                plan['rom'] = (stand, real)
            else:
                plan['copies'].append((real, stand))
            path = os.path.join(folder, leaf)
            if ' ' not in path:
                return path
        if ' ' not in leaf:
            short = _short_path(folder)
            if short and ' ' not in short:
                return os.path.join(short, leaf) if leaf else short
            try:                        # another drive has no relative form
                rel = os.path.relpath(path, cwd) if cwd else path
            except ValueError:
                rel = path
            if cwd and ' ' not in rel and not rel.startswith('..') \
                    and not os.path.isabs(rel):
                run_in = cwd
                return rel
        bad.append(path)
        return path

    for i, arg in enumerate(cmd):
        if flag_before == '-o':
            out.append(fix(arg, 'out'))
        elif i == 0:
            out.append(fix(arg, 'tool'))
        elif arg.startswith('-I') and len(arg) > 2:
            out.append('-I' + fix(arg[2:], 'dir'))
        elif arg.startswith('-'):
            out.append(arg)
        else:
            out.append(fix(arg, 'in'))
        flag_before = arg
    if bad and os.name == 'nt':
        raise ValueError(
            "GBDK cannot use a path with a space in it, and Windows has no "
            "short (8.3) name for: %s. Move the project (or GBDK) to a folder "
            "without spaces." % ", ".join(bad))
    standin = plan if (plan['rom'] or plan['copies']) else None
    return out, run_in, standin


def _gbdk_standin_files(standin_rom: str) -> List[str]:
    """Every file lcc wrote under the stand-in ROM stem (ROM, .map, .noi...)."""
    folder = os.path.dirname(standin_rom)
    stem = os.path.splitext(os.path.basename(standin_rom))[0]
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names
            if os.path.splitext(n)[0] == stem]


def begin_gbdk_standin(standin) -> None:
    """Lay down a stand-in link: drop stale stand-in outputs (they must not be
    renamed over a fresh ROM) and make the spaceless input copies."""
    if not standin:
        return
    if standin['rom']:
        for stale in _gbdk_standin_files(standin['rom'][0]):
            os.remove(stale)
    for real, copy in standin['copies']:
        shutil.copyfile(real, copy)


def finish_gbdk_standin(standin, linked: bool) -> None:
    """Undo a stand-in link: delete the input copies and, when it LINKED,
    rename the ROM and every sibling (`.map`, `.noi`, `.cdb`) to the real
    name. The siblings matter: the bank-window check reads the `.map` beside
    the REAL ROM name. An older output under the real name is replaced."""
    if not standin:
        return
    for _real, copy in standin['copies']:
        try:
            os.remove(copy)
        except OSError:
            pass
    if linked and standin['rom']:
        stand, real = standin['rom']
        real_stem = os.path.splitext(real)[0]
        for path in _gbdk_standin_files(stand):
            os.replace(path, real_stem + os.path.splitext(path)[1])


def gbdk_available() -> bool:
    """Can this machine actually link a GBDK ROM?

    **The one question, asked once.** Every ROM-behaviour test used to answer
    it for itself with `os.path.isdir(ROOT/'gbdk')`, and a directory is not a
    toolchain: a Windows checkout rsynced into the Linux release container
    carries `gbdk/bin/lcc.exe`, which made four suites believe they could
    build and then fail at the link with "GBDK tool 'lcc' not found" - one of
    them reporting three samples as no longer fitting their console. A test
    must ask what the BUILD asks, or "the test thinks it can build" and "the
    build can build" are free to disagree, and the disagreement reads as a
    defect in the game rather than in the machine.
    """
    return GBDKInterface().find_gbdk() is not None


def cc65_available() -> bool:
    """Can this machine actually link a cc65 cartridge? See gbdk_available."""
    return Cc65Interface().find_cc65() is not None


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
        'build': {'output_dir', 'rom_size', 'ram_size', 'bkg_max_tiles',
                  'bkg_strip_w', 'sprite_max_tiles', 'sprite_max_slots',
                  'shake_exports', 'vm_quant', 'code_banks', 'park_updates',
                  'move_lcd', 'frame_lock', 'proj_under_lock',
                  'actor_scan', 'proj_scan', 'actor_deactivate',
                  'actor_pool', 'trigger_pool',
                  'lynx_stack_size', 'lynx_bkg16', 'lynx_code_resident',
                  'bank_bytecode', 'obj_8x16', 'sms_start_button'},
        'assets': {'sprites', 'font'},
        'lib': {'paths'},
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

    def get_sms_start_button(self) -> bool:
        """`[build] sms_start_button` -- the SMS pad's button 1 also answers
        J_START. Default FALSE.

        The pad labels it "1 START" and that is how an SMS title screen is
        started, so a converted game whose title waits on Start is unplayable
        without it. But the two are ONE bit as far as a program is concerned:
        a script attached to BOTH `a` and `start` fires twice on one press -
        the phantom-button hazard `J_SELECT` documents - and that is exactly
        what the SMS/GG sample conversion does, where it opens the title menu twice. So
        it is a per-project choice rather than a console fact, and the
        console's PAUSE button stays a real Start either way.
        """
        value = self.config.get('build', {}).get('sms_start_button', False)
        if not isinstance(value, bool):
            raise ValueError(
                "invalid sms_start_button '%s' (expected true or false)" % value)
        return value

    def get_obj_8x16(self) -> bool:
        """`[build] obj_8x16` -- 8x16 OBJ sprite mode (the reference engine runs it).

        A GLOBAL sprite-size mode: every hardware sprite is 8x16, so a
        WxH-tile metasprite costs W*(H/2) OAM objects instead of W*H -- which
        is what lets a 7x6 boss (42 objects at 8x8, over the GB's 40) draw at
        21. Real on the GB family (LCDC bit 2) AND on the SMS / Game Gear
        (VDP R1's sprite-size bit): both hardwares pair consecutive patterns
        vertically and ignore the index's low bit, so one column-major tile
        stream serves both. Sprite tile data reorders to COLUMN-major per
        frame at build time, so every sheet must be a MANIFESTED named sheet
        with even tile heights (the conversion refuses otherwise). Honestly
        ignored on a console without the mode (`platforms.OBJ16_CONSOLES`).
        Off = byte-identical."""
        value = self.config.get('build', {}).get('obj_8x16', False)
        if not isinstance(value, bool):
            raise BuildConfigError(
                "invalid obj_8x16 '%s' (expected true or false)" % value)
        return value

    def get_font_file(self) -> Optional[str]:
        """`[assets] font` -- a custom console-font PNG for the GLYPH-BUFFER
        text mode (a row-major 8x8 glyph grid whose first 96 cells are ASCII
        32..127). Converted to a 1bpp table at build time and baked into the
        prelude in place of the built-in font, so it costs nothing at runtime.
        Only meaningful for a program that calls text.glyph_buffer; the
        resident-font path keeps text.set_font for its runtime swap."""
        return self.config.get('assets', {}).get('font')

    def get_lib_paths(self) -> List[str]:
        """Extra library search roots ([lib] paths), relative to the project file.

        These join the built-in roots (MOSAIK_LIB, then the default `lib/`
        next to the tool) so `import "engine.camera"` can resolve to a shared
        library copy instead of being vendored. See
        MosaikBuilder._lib_search_roots.
        """
        paths = self.config.get('lib', {}).get('paths', [])
        return list(paths) if isinstance(paths, list) else [paths]

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

    def get_bkg_max_tiles(self) -> Optional[int]:
        """`[build] bkg_max_tiles` -- the resident background tile-table budget
        for the cc65 Lynx bkg engine, or None when unset (the 256 default).

        Lowering it reclaims (256-N)*16 bytes of the scarce ~46.6 KB Lynx MAIN
        (a full table is 4 KB of BSS). The value MUST be >= the world's real
        tile count (including any top-of-table animated/REPLACE_TILE hidden
        indices), the same author-owned contract as `rom_size`. Byte-identical
        on every console when unset or 256 (only the two Lynx bkg engines read
        it; other consoles keep the full 256). Raises ValueError out of 1..256.
        """
        value = self.config.get('build', {}).get('bkg_max_tiles')
        if value is None:
            return None
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError("invalid bkg_max_tiles '%s' (expected an integer 1..256)"
                             % value)
        if not (1 <= n <= 256):
            raise ValueError("invalid bkg_max_tiles %d (must be 1..256)" % n)
        return n

    def get_bkg_strip_w(self) -> Optional[int]:
        """`[build] bkg_strip_w` -- the cc65 Lynx ROW-strip bkg engine's strip
        width in tiles, or None when unset (the full scroll-period default).

        The row-strip engine draws each visible map row as one screen-spanning
        literal strip; the default width covers the whole 256 px scroll period
        (52 tiles on the Lynx) so a single strip covers any horizontal scroll.
        A world whose scenes never scroll that far can lower it to reclaim
        STRIPS*(full-N)*16 bytes of the scarce Lynx MAIN (the strip ring is
        ~13.5 KB of BSS at the default). The value MUST cover the widest scene's
        scroll (>= max_scene_w + 1 tiles), the same author-owned contract as
        `bkg_max_tiles`; a VM8 game derives it automatically. Byte-identical
        when unset (only the Lynx row-strip engine reads it). Raises ValueError
        out of 16..64.
        """
        value = self.config.get('build', {}).get('bkg_strip_w')
        if value is None:
            return None
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError("invalid bkg_strip_w '%s' (expected an integer 16..64)"
                             % value)
        if not (16 <= n <= 64):
            raise ValueError("invalid bkg_strip_w %d (must be 16..64)" % n)
        return n

    def get_lynx_bkg16(self) -> bool:
        """`[build] lynx_bkg16` -- force the 4bpp (16-colour) Atari Lynx background
        engine for a HAND-WRITTEN game. A scenes/VM8 game uses `[world]
        lynx_bkg16` (the transpiler forks + the depth is auto-detected from the
        tileset); this flag is for a hand-written program that supplies its own
        4bpp tiles + calls palette.load_bkg16. Off (default) = the 2bpp path,
        byte-identical. Doubles the Lynx bkg RAM; size it with bkg_max_tiles /
        bkg_strip_w. Ignored on every non-Lynx console."""
        return bool(self.config.get('build', {}).get('lynx_bkg16', False))

    def get_lynx_code_resident(self) -> bool:
        """`[build] lynx_code_resident` -- keep the VM8 bytecode blob RESIDENT in
        Atari Lynx RAM instead of streaming it from the cart.

        The interpreter fetches that blob one byte at a time, so it is the only
        streamed asset whose access pattern is pathological for a single-page
        cache: the scheduler round-robins several threads whose program counters
        sit in different 256 B pages, and a bytecode CALL crosses one too, so the
        page is refilled constantly. Measured with the GearLynx debugger on
        the falling-block assembly sample: a refill is ~98,000 ticks (0.42 of an LCD frame -- the
        cart reads at ~96 CPU cycles a byte), there is roughly one per thread
        slice, and turning this on took a VM frame from 3 LCD frames to 2.

        The price is the blob's size in the scarce Lynx MAIN (~2 KB for
        the falling-block assembly sample), which is why it is a knob and not a default: a big-world game
        streams precisely because it has no MAIN to spare. Off = the streamed
        default, byte-identical. Ignored on every non-Lynx console."""
        return bool(self.config.get('build', {}).get('lynx_code_resident', False))

    def get_bank_bytecode(self) -> bool:
        """`[build] bank_bytecode` -- put the VM8 bytecode blob in a ROM BANK on the
        GB family instead of the resident image. The mirror of the Lynx knob above,
        and the opposite trade.

        `_scripts_CODE` is the biggest single resident symbol a VM8 game has, and
        the reference engine's own bank 0 carries no bytecode at all. Banking it frees that
        whole blob of bank 0 (2,748 B on the reference-engine conversion) at the cost of a
        SWITCH_ROM per FETCHED BYTE, so what it costs depends entirely on how much
        of the game's logic is bytecode: measured with PyBoy CPU hooks, an
        event-script game runs ~0 bytecode instructions in a steady-state frame and
        peaks near 0.5% of an LCD frame, while a game written wholly in bytecode
        (the falling-block assembly sample) pays ~28% at its peak.

        Needs `[build] code_banks` -- without code banking there is no bank to
        switch to and the blob stays resident. Off = byte-identical; ignored
        (noted) on a console without banking."""
        return bool(self.config.get('build', {}).get('bank_bytecode', False))

    def get_sprite_max_tiles(self) -> Optional[int]:
        """`[build] sprite_max_tiles` -- the cc65 Lynx Suzy sprite tile-table
        budget (`gbs_tiles[N][GBS_TILE_BYTES]`), or None when unset (the 40
        default, auto-derived from the tiles the program uploads).

        The Lynx engine allocates a resident 40-tile table (~1.3 KB of BSS,
        converted from the 2bpp source at runtime). A program that uploads
        fewer tiles can lower N to reclaim (40-N)*33 bytes of the scarce Lynx
        MAIN; the value MUST be >= the highest tile index the program uploads
        via sprite.set_data (the same author-owned contract as bkg_max_tiles).
        Byte-identical when unset or 40 (only the Lynx sprite engine reads it).
        Raises ValueError out of 1..40.
        """
        value = self.config.get('build', {}).get('sprite_max_tiles')
        if value is None:
            return None
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError("invalid sprite_max_tiles '%s' (expected an integer 1..40)"
                             % value)
        if not (1 <= n <= 40):
            raise ValueError("invalid sprite_max_tiles %d (must be 1..40)" % n)
        return n

    def get_sprite_max_slots(self) -> Optional[int]:
        """`[build] sprite_max_slots` -- the cc65 Lynx sprite SLOT budget
        (`gbs_scb[N]` + the per-slot tile/meta side tables), or None when unset
        (all 40, byte-identical).

        The Suzy engine mirrors the GB's 40 OAM objects so a slot id means the
        same thing on every console, but each slot costs ~27 bytes of the
        scarce Lynx MAIN. A game that knows its highest slot can lower N and
        reclaim the rest: the VM8 pool is actors 0..7, the player at 8 and
        projectiles from 16, so a game with no projectiles needs 9. Every slot
        op is guarded by `nb < GBS_MAX_SPRITES`, so a slot past N is a no-op
        (an author-owned contract, like sprite_max_tiles) rather than a stray
        write. NEVER auto-derived -- slot ids are runtime values.
        Raises ValueError out of 1..40.
        """
        value = self.config.get('build', {}).get('sprite_max_slots')
        if value is None:
            return None
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError("invalid sprite_max_slots '%s' (expected an integer 1..40)"
                             % value)
        if not (1 <= n <= 40):
            raise ValueError("invalid sprite_max_slots %d (must be 1..40)" % n)
        return n

    def get_pool_defines(self) -> dict:
        """`[build] actor_pool` / `trigger_pool` -- the VM8 runtime slot pools.

        A room may hold at most `actor_pool` placed objects and `trigger_pool`
        triggers (`generate_rooms` refuses more, at GENERATION time). Both
        default to 8, which is byte-identical; the reference engine's own limit is 20
        actors, so that is the parity target for an import.

        Growing them costs BSS on EVERY console, which is why this is a knob
        rather than a bigger constant -- measured on the GB at 20: +29 B per
        actor slot (vm.actor + vm.entity + vm.canim; +15 more when a project
        links vm.clip) and +9 B per trigger slot, with zero code growth. The
        tight Lynx samples keep the default and are unaffected.

        Returns only the keys actually set, so an unset project passes nothing
        and `POOL_DEFINE_DEFAULTS` supplies the historical sizes.
        Raises ValueError out of 1..255 (the runtime indexes slots with a u8,
        and 255 is `vm.entity`'s "no thread" sentinel).
        """
        out = {}
        for key, define in (('actor_pool', 'VM_ACTOR_POOL'),
                            ('trigger_pool', 'VM_TRIGGER_POOL')):
            value = self.config.get('build', {}).get(key)
            if value is None:
                continue
            try:
                n = int(value)
            except (TypeError, ValueError):
                raise ValueError("invalid %s '%s' (expected an integer 1..255)"
                                 % (key, value))
            if not (1 <= n <= 255):
                raise ValueError("invalid %s %d (must be 1..255)" % (key, n))
            out[define] = n
        # engine.anim's pool is indexed BY ACTOR SLOT (vm.canim), so it must
        # cover the actor pool -- raising actor_pool alone used to leave
        # actors 8.. writing past every array in engine.anim. Never SHRINKS
        # below the shipped 8, so a small actor_pool cannot break a non-VM
        # game that uses the animators directly.
        if "VM_ACTOR_POOL" in out:
            out["VM_ANIM_SLOTS"] = max(8, out["VM_ACTOR_POOL"])
        return out

    def get_shake_exports(self) -> bool:
        """`[build] shake_exports` -- opt-in declaration-level tree-shaking.

        When true, module-level functions/vars unreachable from main() are
        dropped before codegen (MosaikCompiler._shake_declarations), so an
        exported-but-unused lib surface stops costing ROM + (cc65
        --static-locals) BSS in the linked image. Off by default: shaking
        changes the generated C, and default output stays byte-identical
        (golden-pinned). Raises ValueError for a non-boolean value.
        """
        value = self.config.get('build', {}).get('shake_exports', False)
        if not isinstance(value, bool):
            raise ValueError(
                "invalid shake_exports '%s' (expected true or false)" % value)
        return value

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

    def get_lynx_stack_size(self) -> int:
        """`[build] lynx_stack_size` -- the Atari Lynx C-stack size in BYTES (default 512).

        The Lynx links with a small C stack (the `__STACKSIZE__` weak symbol) instead of
        cc65's 2 KB default, because MAIN is the one scarce Lynx area -- so the stack TRADES
        against code/data room. 512 B fits every shipped sample (incl. a streamed imported
        song); a game with deeper call chains can RAISE it (at the cost of MAIN), and a game
        hard against the MAIN ceiling can LOWER it. Accepts a decimal or 0x-hex value; clamped
        to a sane 128..4096 B. Lynx-only (ignored on every other console)."""
        value = self.config.get('build', {}).get('lynx_stack_size')
        if value is None:
            return 512
        try:
            n = int(str(value).strip(), 0)          # decimal or 0x-hex
        except (TypeError, ValueError):
            raise ValueError(
                "invalid lynx_stack_size '%s' (a byte count, e.g. 512 or 0x200)" % value)
        if not 128 <= n <= 4096:
            raise ValueError(
                "lynx_stack_size %d out of range (128..4096 bytes)" % n)
        return n

    def get_vm_quant(self) -> int:
        """`[build] vm_quant` -- VM8 instructions per thread per frame (default 16).

        Spec 14 pins QUANT at 16 and explicitly lets a BUILD raise it. Raising it
        is a CORRECTNESS lever, not just a speed one: a bytecode program that keeps
        scratch state in shared heap cells across a CALL chain (the classic
        `set args; CALL check; read result` idiom) needs its whole critical section
        to finish inside ONE slice. At 16, a second thread interleaves midway and
        clobbers the args -- the falling-block assembly sample is exactly this, which is why the
        reference program's header asks for >= 512.

        Accepts 16 (the spec minimum, the byte-identical default) or 512. It is a
        two-value switch rather than a free integer because it lowers to one
        compile-time flag folded into `lib/vm/core.mos`."""
        value = self.config.get('build', {}).get('vm_quant')
        if value is None:
            return 16
        try:
            n = int(str(value).strip(), 0)
        except (TypeError, ValueError):
            raise ValueError(
                "invalid vm_quant '%s' (expected 16 or 512)" % value)
        if n not in (16, 512):
            raise ValueError(
                "vm_quant %d unsupported (expected 16, the spec minimum, or 512)" % n)
        return n

    def get_frame_lock(self) -> int:
        """`[build] frame_lock` -- hold a game frame to N DISPLAY frames (default 1 = off).

        A VM frame is not a display frame, and it is not a CONSTANT number of
        them either: measured 2026-08-30, the reference-engine sample conversion's 17 rooms span 1.00 to
        2.14 LCD frames per game frame and the platformer conversion's six span 1.17 to 3.53.
        That is a problem for a CONVERSION, whose durations and velocities are
        authored in the reference engine's display frames and converted by one constant
        (`VM_FRAMES_PER_LCD`): a single scale can only be right where the ratio
        equals it, so some room is always off - and it is off in the direction
        that punishes optimisation, since a room taken from 2.00 to 1.00 then
        runs its whole game at double the reference speed.

        Locking makes the ratio the constant the conversion already assumes.
        It is a FLOOR, not a ceiling - a frame that overran does not wait, as
        the reference engine's own `wait_vbl_done` does not - so it trades the frame RATE
        of the rooms that were fast for a uniform, correct game SPEED.

        1 (the default) is off and byte-identical. 2..8 is the lock."""
        value = self.config.get('build', {}).get('frame_lock')
        if value is None:
            return 1
        try:
            n = int(str(value).strip(), 0)
        except (TypeError, ValueError):
            raise ValueError(
                "invalid frame_lock '%s' (expected 1..8)" % value)
        if not 1 <= n <= 8:
            raise ValueError(
                "frame_lock %d out of range (expected 1 = off, or 2..8)" % n)
        return n

    def get_proj_under_lock(self) -> bool:
        """`[build] proj_under_lock` -- projectiles keep flying under a cutscene
        LOCK, which is what the reference engine does (default false).

        The reference VM's `src/core/core.c` runs `projectiles_update()` OUTSIDE its
        `!VM_ISLOCKED()` block: a lock freezes input, the player state machine,
        timers and music events, and nothing else. So a shot already in flight
        when a cutscene starts keeps travelling and keeps colliding there.
        Ours freezes it in the air - and inconsistently with its own frame,
        since `actor.step_all`, the emote, the background animation and the
        music driver all run under the lock.

        A SEMANTIC knob rather than a fix, because it changes what a script may
        assume: a cutscene opened while an enemy shot is on screen can now be
        interrupted by that shot landing."""
        return bool(self.config.get('build', {}).get('proj_under_lock', False))

    def get_park_updates(self) -> bool:
        """`[build] park_updates` -- offscreen On Update parking (the reference engine's
        actor-deactivation model). When true, `vm.entity` kills the On Update
        thread of any actor parked off the visible window (or retired) and
        respawns the script FROM THE TOP when the camera brings it back. A
        SEMANTIC opt-in, not just a speed lever: an On Update may no longer
        assume it keeps running while its actor is offscreen (a patrol freezes
        where the camera left it, exactly as in the reference engine). Lowers to one
        compile-time flag (VM_UPDATE_ALWAYS = false) folded into
        `lib/vm/entity.mos`; absent/false is byte-identical."""
        value = self.config.get('build', {}).get('park_updates')
        if value is None:
            return False
        if not isinstance(value, bool):
            raise ValueError(
                "invalid park_updates '%s' (expected true or false)" % value)
        return value

    def get_move_lcd(self) -> bool:
        """`[build] move_lcd` -- pace a native actor auto-move by elapsed
        DISPLAY frames instead of once per call (the reference engine's own unit).

        Its actors step `move_speed` px per LCD frame, but a VM frame is not a
        display frame (spec R3) and the ratio is a property of the ROOM: the
        importer's fixed 2x is right for a heavy room and twice too fast in a
        light one, which is why the shooter conversion's falling hazard fell in half the reference
        time. With this on, `a_speed` IS px per LCD frame and no conversion
        factor is guessed. Lowers to one compile-time flag
        (VM_MOVE_PER_CALL = false) folded into `lib/vm/actor.mos`;
        absent/false is byte-identical."""
        value = self.config.get('build', {}).get('move_lcd')
        if value is None:
            return False
        if not isinstance(value, bool):
            raise ValueError(
                "invalid move_lcd '%s' (expected true or false)" % value)
        return value

    def get_actor_scan(self) -> int:
        """`[build] actor_scan` -- how often a PARKED actor is re-tested against
        the visible window (default 1 = every actor every frame, the historical
        behaviour and byte-identical).

        Deciding a slot is off-window is the fixed per-live-actor cost of
        `vm.actor.render`, and on a wide level most of the pool is parked most
        of the time: measured on a converted shooter room, that pass
        cost ~2,500 cycles per live actor per frame whether or not anything was
        on screen. The reference engine amortises the same test one actor in four
        (`(tmp_iterator++ & 0x3) == 0`, core/actor.c).

        N is a power of two (1/2/4/8) because the runtime takes it as a mask.
        The only behaviour it changes is that a parked actor WAKES up to N-1
        frames late; a visible actor is always tested, so nothing can be drawn
        at a stale position."""
        value = self.config.get('build', {}).get('actor_scan')
        if value is None:
            return 1
        try:
            n = int(str(value).strip(), 0)
        except (TypeError, ValueError):
            raise ValueError(
                "invalid actor_scan '%s' (expected 1, 2, 4 or 8)" % value)
        if n not in (1, 2, 4, 8):
            raise ValueError(
                "actor_scan %d unsupported (expected 1, 2, 4 or 8 -- the "
                "runtime takes N-1 as a mask, so it must be a power of two)"
                % n)
        return n

    def get_proj_scan(self) -> int:
        """`[build] proj_scan` -- how often a shot in flight is COLLISION-tested
        (default 1 = every shot every frame, the historical behaviour and
        byte-identical).

        This is the reference engine's own amortisation and its default is FOUR:
        `projectiles.c` defines `PROJECTILES_COLLISION_SPREAD SPREAD_4` and
        wraps the whole hit test in `tmp_iterator = game_time; ... if
        ((tmp_iterator++ & PROJECTILES_COLLISION_SPREAD) == 0)`, the same
        phase-spread shape as its actor scan. We tested every shot every frame,
        which is a straight 4x on the stage the frame histogram blames for
        a shooter room's crossing frames (`projectile_update` 783 -> 15,492
        between its 2- and 3-LCD buckets).

        N is a power of two (1/2/4/8) because the runtime takes it as a mask.
        What it changes is real and is what the reference accepts: a shot is
        tested at up to N-1 frame intervals, so a fast shot can pass THROUGH a
        thin target between two tests. Only the collision test is spread -
        movement, the off-screen despawn, the lifetime countdown and the flight
        animation all still run every frame, exactly as they do in the reference VM."""
        value = self.config.get('build', {}).get('proj_scan')
        if value is None:
            return 1
        try:
            n = int(str(value).strip(), 0)
        except (TypeError, ValueError):
            raise ValueError(
                "invalid proj_scan '%s' (expected 1, 2, 4 or 8)" % value)
        if n not in (1, 2, 4, 8):
            raise ValueError(
                "proj_scan %d unsupported (expected 1, 2, 4 or 8 -- the "
                "runtime takes N-1 as a mask, so it must be a power of two)"
                % n)
        return n

    def get_actor_deactivate(self) -> bool:
        """`[build] actor_deactivate` -- the reference engine's OFFSCREEN DEACTIVATION: an
        actor the camera has left behind is taken OUT of the list every
        per-frame pass walks, instead of staying in it and being skipped.

        The reference VM's `actors_update` bounds check ends in `deactivate_actor_impl`,
        which removes the actor from `actors_active_head` (core/actor.c);
        measured on the reference ROM, its shooter room walks FIVE actors where
        ours walks sixteen.

        A SEMANTIC knob, off by default, and the divergence is the reference VM's own: an
        actor off the list does not auto-move, is not collided against and
        cannot be hit, so only the CAMERA brings it back. The reference makes
        that explicit (re-activation is driven from the scroll); ours re-tests
        the parked list on the `[build] actor_scan` phase, so the wake LATENCY
        is unchanged and a wake needs only a camera move rather than a freshly
        revealed row."""
        value = self.config.get('build', {}).get('actor_deactivate')
        if value is None:
            return False
        if not isinstance(value, bool):
            raise ValueError(
                "invalid actor_deactivate '%s' (expected true or false)" % value)
        return value

    def get_code_banks(self) -> List[str]:
        """`[build] code_banks` -- cold-code ROM banking on the GB family
        A list of module names (e.g. ["vm.music"]) whose
        function bodies are placed in a switchable ROM bank, freeing resident
        bank-0 room for content. The codegen keeps the hot interpreter and
        every seam-registered callback ENTRY resident automatically (stubs),
        and refuses 'scripts' outright; 'vm.core' may be listed and banks only
        its cold half (it `bank(0)`-pins its own hot path, gen_decls.py). Empty/absent = off, and the
        build stays byte-identical. Ignored (with a note) on consoles without
        banked-ROM support."""
        value = self.config.get('build', {}).get('code_banks')
        if value is None:
            return []
        if (not isinstance(value, list)
                or not all(isinstance(v, str) for v in value)):
            raise ValueError(
                "invalid code_banks '%s' (expected a list of module names, "
                'e.g. ["vm.music"])' % (value,))
        return value

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

        # Priority 1b: the setup_tools install prefix. An INSTALLED studio
        # cannot fetch toolchains into its own (read-only) tree, so they land
        # under $MOSAIK8_HOME instead; setup_tools.py reads the same variable.
        prefix = tool_prefix()
        if prefix:
            cand = os.path.join(prefix, 'gbdk')
            if self.validate_gbdk_installation(cand):
                return cand

        # Priority 2: Relative paths (common in GBDK-2020 examples).
        # Resolved against the CWD *and* against this file, the way find_cc65
        # already does it: the bundled `gbdk/` sits beside mosaik8_build.py,
        # and a caller that is not chdir'd into the engine root (any test, for
        # one) would otherwise miss a toolchain that is plainly there.
        here = os.path.dirname(os.path.abspath(__file__))
        relative_paths = ['./gbdk-2020', '../gbdk-2020', './gbdk', '../gbdk']
        bases = [os.getcwd(), here]
        for base in bases:
            for rel_path in relative_paths:
                abs_path = os.path.abspath(os.path.join(base, rel_path))
                if os.path.exists(abs_path):
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
            try:
                cmd, run_in, standin = gbdk_spaceless_command(
                    cmd, os.path.dirname(os.path.abspath(output_file)))
            except ValueError as e:
                print(f"    ❌ Error: {e}")
                return False
            begin_gbdk_standin(standin)

            print(f"Compiling with {self.version_info.get('type', 'GBDK')}: {' '.join(cmd)}"
                  + (f"  (in {run_in})" if run_in else ""))
            result = None
            try:
                result = subprocess.run(cmd, capture_output=True, text=True,
                                        cwd=run_in)
            finally:
                finish_gbdk_standin(standin, linked=result is not None
                                    and result.returncode == 0)

            if result.returncode != 0:
                print(f"Compilation failed:")
                print("STDOUT:", result.stdout)
                print("STDERR:", result.stderr)

                # Provide version-specific hints
                self._provide_error_hints(result.stderr)
                return False

            # AN UNDEFINED GLOBAL IS A LINKER *WARNING* AND sdldgb STILL EXITS 0,
            # writing a ROM whose call to the missing symbol goes to address 0.
            # That is the same silent-corruption class as a bank overflow, and it
            # is easy to reach: a prelude helper moved into a bank TU keeps its
            # `static` by accident, sdcc drops the unused static, and the only
            # sign is two lines in a build log nobody reads (measured, W7d phase
            # 2). Fail here instead.
            undef = [ln.strip() for ln in
                     (result.stdout + "\n" + result.stderr).splitlines()
                     if "Undefined Global" in ln]
            if undef:
                print("    ❌ Link failed: the ROM references symbols nothing "
                      "defines. sdldgb reports these as warnings and writes the "
                      "ROM anyway; a call to one of them jumps to address 0.")
                for ln in undef[:12]:
                    print("       " + ln)
                if len(undef) > 12:
                    print("       ... and %d more" % (len(undef) - 12))
                return False

            return True

        except Exception as e:
            print(f"Error running GBDK tools: {e}")
            return False

    def _provide_error_hints(self, stderr: str):
        """Provide helpful error hints for a failed GBDK build."""
        hints = []
        low = stderr.lower()

        if "not found" in low:
            hints.append("• Check GBDK installation path")
            hints.append("• Verify GBDK_HOME environment variable")

        # makebin: the resident image exceeds the cart. On the GB family / SMS a 32 KB
        # cart holds ~15 KB of VM/framework code, so a LARGE resident const (typically a
        # big imported SONG read via assets.code_byte, which stays RESIDENT on the
        # directly-mapped consoles) overflows it. Growing rom_size does NOT help a
        # resident blob (it SHRINKS the fixed bank), so say the actionable things instead.
        if "too large for number of banks" in low or "rom is too large" in low:
            hints.append("• The ROM exceeds the cart. This is usually a LARGE resident "
                         "const -- most often a big imported SONG (scripts/songs.toml), "
                         "which is RESIDENT on the GB family / SMS / GG.")
            hints.append("• A big song fits the ATARI LYNX (it streams the song data from "
                         "the cart), so target lynx, OR shorten the song (fewer patterns/"
                         "rows) so it fits the ~32 KB cart on the directly-mapped consoles.")
            hints.append("• Enable [build] shake_exports = true (trims unused lib code) if "
                         "it is close.")
        elif "bank" in low:
            hints.append("• A banked const array must fit a single 16 KB ROM bank; split "
                         "the data if larger.")

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

        # Priority 1b: the setup_tools install prefix ($MOSAIK8_HOME), for an
        # installed studio whose own tree is read-only. See find_gbdk.
        prefix = tool_prefix()
        if prefix:
            cand = os.path.join(prefix, 'cc65')
            if self.validate_cc65_installation(cand):
                return cand

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
        """The cl65 driver in `path/bin`, or None.

        The `.exe` name is accepted only ON Windows, exactly as
        `GBDKInterface._find_tool_in_dir` does it. Accepting it everywhere
        made a WINDOWS cc65 tree validate on Linux, and the build then got as
        far as exec'ing a PE binary: `[Errno 13] Permission denied:
        '.../cc65/bin/cl65.exe'`. That is how a Windows checkout rsynced into
        the Linux release container reported three samples as no longer
        fitting their console - the link never ran at all.
        """
        bin_dir = os.path.join(path, 'bin')
        names = ('cl65', 'cl65.exe') if os.name == 'nt' else ('cl65',)
        for name in names:
            cand = os.path.join(bin_dir, name)
            if os.path.isfile(cand):
                return cand
        return None

    def is_available(self) -> bool:
        return self.cc65_path is not None

    def link_target(self, c_files: List[str], output_file: str, cc65_target: str,
                    debug: bool = False,
                    extra_flags: Optional[List[str]] = None,
                    stack_size: Optional[int] = None) -> bool:
        """Compile and link the given C files into a cartridge image via cl65. ``stack_size``
        (bytes) overrides the Lynx C-stack (`__STACKSIZE__`); None = the 512 B default."""
        if not c_files:
            return False
        if not self.is_available():
            print("Error: cc65 toolchain not found.")
            print("  Set CC65_HOME, bundle a cc65/ folder next to mosaik8.py, "
                  "or put cl65 on PATH.")
            return False

        cl65 = self._find_cl65(self.cc65_path)
        # `--static-locals` places C locals at absolute addresses instead of on
        # the expensive 6502 C-stack, so the code to touch them is smaller (CODE
        # shrinks well over the small BSS it adds) -- a net win that recovers the
        # ~346 B platform-quest overflowed the Lynx MAIN area by. It is a cc65
        # *link* flag (no generated-C change, so byte-identical guarantees hold);
        # GBDK consoles don't go through here. Cost = non-reentrancy: safe here
        # (mosaik has no closures/recursion, the callback paths are ordinary
        # calls). Do NOT add `-Oi`/`-Os` -- measured WORSE (inlining trades
        # BSS-cheap calls for CODE bloat, growing the overflow).
        flags = ['-t', cc65_target, '-O', '--static-locals', *(extra_flags or [])]
        # The Lynx links with a 640 B C stack instead of cc65's default 2 KB
        # (`__STACKSIZE__` is a weak linker symbol): with `--static-locals` the C
        # stack holds only call arguments + a few compiler temps (return addresses
        # ride the 6502 hardware stack, mosaik has no recursion, and the deepest
        # composed call chains push well under 200 bytes -- the cc65 runtime's own
        # non-static locals included), so the reserved 2 KB was almost entirely
        # wasted -- and MAIN is the ONE scarce Lynx area (46.6 KB for code + rodata
        # + bss). The freed ~1.4 KB absorbed the music driver's hUGE-parity growth
        # AND leaves room for a streamed imported song's resident side (page cache
        # + row tables). 512 B is the default; a project can override it in the Build
        # panel / `[build] lynx_stack_size` (raise for deeper call chains, lower to
        # reclaim MAIN). Link-time only (the generated C is unchanged); the PCE keeps
        # its default.
        if cc65_target == 'lynx':
            flags.extend(['-Wl', '-D__STACKSIZE__=0x%04X' % (stack_size or 512)])
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


def _vm_dispatch_defines(sources):
    """Compile-time flags that fold `lib/vm/core.mos`'s dispatch down to the
    opcodes / RPN tokens THIS program's bytecode actually contains.

    `vm_core_step` is one arm per opcode and `rpn_eval` one per token; neither
    can be reached by tree-shaking, which works at declaration level. But a
    VM8 game's blob is fixed at build time, and a typical one uses well under a
    quarter of the instruction set, so the rest is dead weight -- worth several
    KB on the Atari Lynx, whose single ~46.6 KB MAIN is the binding constraint.

    Returns {"VM_OP_<NAME>": bool, "VM_RPN_<NAME>": bool, "VM_ST_<NAME>": bool}
    for EVERY opcode, token and engine state (an absent name would stay
    unresolvable and keep its arm, so the false ones have to be stated). The
    VM_ST_ flags fold state UPKEEP, not a dispatch arm: a state whose value has
    to be maintained every frame (`game_time`'s counter) costs nothing in a
    program that never reads it. Returns {} -- meaning "keep everything,
    byte-identical" -- whenever the program is not a VM8 game, the blob cannot
    be decoded, or `mosaik_vm` is unavailable.
    """
    try:
        import mosaik_vm
        from mosaik_vm.rooms.emit_start import FADE_STYLE_MARKER
    except Exception:      # noqa: BLE001 - the VM toolchain is optional here
        return {}
    ops = toks = states = None
    for _fn, text in sources:
        if "const CODE" not in text:
            continue
        o, t, s = mosaik_vm.scan_scripts_module(text)
        if o is None:
            return {}      # a blob we cannot read: keep every arm
        ops = o if ops is None else (ops | o)
        toks = t if toks is None else (toks | t)
        states = s if states is None else (states | s)
    if ops is None:
        return {}          # not a VM8 game
    defines = {"VM_OP_%s" % name: (name in ops) for name in mosaik_vm.OPS}
    defines.update({"VM_RPN_%s" % name: (name in toks) for name in mosaik_vm.RPN})
    defines.update({"VM_ST_%s" % name.upper(): (sid in states)
                    for name, sid in mosaik_vm.STATES.items()})
    # FADE DIRECTION: vm.fx's white ramp and graphics.palette's white scale
    # fold away unless something can ask for them - a blob that writes the
    # `fade_style` state, or a generated rooms.mos that declares the project's
    # `[scenes] fade_style` default (a CALL to fx.set_style, not a state, so
    # the per-use scan above cannot see it). The marker is the DECLARATION
    # COMMENT rooms.mos emits beside that call, never the call itself:
    # `lib/vm/core.mos` calls fx.set_style from its SET_STATE arm, so scanning
    # for the call matched every VM8 game and the white arms never folded
    # (measured: all 13 A/B ROMs differed). Both towards-black arms stay
    # character for character when neither exists.
    defines["VM_FADE_STYLE"] = bool(
        defines.get("VM_ST_FADE_STYLE")
        or any(FADE_STYLE_MARKER in text for _fn, text in sources))
    # THE CAMERA FOLLOW OPTIONS (W7b). Two derived flags, because the per-use
    # scan cannot answer either question on its own:
    #
    #  * VM_CAM_PROPS folds the SET_STATE range for the four camera properties
    #    (dead zone x/y, follow offset x/y). The consecutive-id precedent
    #    (`camera_min_x`) states its flag from the FIRST id because a script
    #    sets all four together - the reference engine's EVENT_CAMERA_SET_BOUNDS writes
    #    them in one go. EVENT_CAMERA_PROPERTY_SET writes ONE property per use,
    #    so here the flag has to be the OR of all four or a project that only
    #    offsets its camera would lose the arm that does it.
    #
    #  * VM_CAM_FOLLOW_OPTS keeps `vm.player`'s per-axis follow (dead zone,
    #    offset, per-axis lock, preventScroll) out of a project that cannot
    #    reach a non-default value. `camera_settings` defaults to 3 (follow
    #    both) and the properties to 0, and the only producers of anything else
    #    are a write to the settings byte or a write to one of the properties -
    #    so with neither in the blob the plain follow is not merely equivalent,
    #    it is the same camera. That keeps the follow path character for
    #    character for every project that does not tune its camera, which
    #    matters on the Lynx and the PC Engine: `vm.player` is banked on the GB
    #    family, but cc65 links one flat MAIN.
    defines["VM_CAM_PROPS"] = any(
        defines.get("VM_ST_CAMERA_%s" % n)
        for n in ("DEADZONE_X", "DEADZONE_Y", "OFFSET_X", "OFFSET_Y"))
    #    ...and a GENERATED ROOMS.MOS that writes a property counts too, which
    #    the blob scan above cannot see: a `pointnclick` scene's dispatch arm
    #    emits `player.set_cam_prop` for the reference's
    #    POINT_N_CLICK_CAMERA_DEADZONE (W7j), and with the follow options
    #    folded away that dead zone would be silently ignored - the camera
    #    would track the cursor pixel for pixel. The world fact that emits the
    #    CALL is what states the flag, exactly as VM_TRIG_NO_FORCE does.
    defines["VM_CAM_FOLLOW_OPTS"] = bool(
        defines["VM_CAM_PROPS"]
        or defines.get("VM_ST_CAMERA_LOCK")
        or any("player.set_cam_prop(" in text for _fn, text in sources))
    # SPARSE metasprite frames: `vm.canim` draws through sprite.set_meta_mask
    # only when the generated clips module has a per-frame blank mask. The
    # masked prelude is ~470 B of RESIDENT image (it cannot bank), and a
    # by-use scan cannot see that the call is unreachable - the call is there
    # in the library source either way. So the flag is stated here, exactly
    # like the dispatch arms, and a world of solid rectangles stays
    # byte-identical.
    defines["VM_META_MASK"] = any("function frame_mask(" in text
                                  for _fn, text in sources)
    # DESCRIPTOR frames, the same rule: `vm.canim` routes a desc kind's upload
    # through the generated clips.draw (sprite.set_meta_list) only when the
    # clips module carries descriptors, and the list prelude + seam are
    # resident bytes a non-desc world must not pay.
    defines["VM_META_LIST"] = any("function is_desc(" in text
                                  for _fn, text in sources)
    # PER-FRAME SPRITE PALETTE, the same rule again, and here it is the whole
    # palette engine at stake: `vm.canim` calls sprite.set_palette only when
    # the generated clips module carries a per-frame palette, but a by-use scan
    # sees the call in the library source and turns on `palette_imported` -
    # which emits the sprite-palette prelude AND makes set_meta / set_prop
    # merge through GBS_KEEP_PAL, in every animated game whether it colours
    # anything or not.
    defines["VM_CLIP_PAL"] = any("function frame_pal(" in text
                                 for _fn, text in sources)
    # BATCHED clip selector, the same rule: `vm.canim.apply` reads the drawn
    # frame's tile + FLIP_X + is-descriptor through ONE generated `clips.sel`
    # call (three banked reads on every animator-step frame otherwise), and
    # only a clips module regenerated since the selector exists carries it.
    # Off, apply keeps its original arm character for character, so every
    # project with an older generated clips module is byte-identical.
    defines["VM_CLIP_SEL"] = any("function sel(" in text
                                 for _fn, text in sources)
    # TRIGGER ON LEAVE, the same rule once more: `vm.trigger` keeps a SECOND
    # entry table and a falling-edge spawn only for a world that binds one, and
    # the generated scenes module carries a `trigger_leave` selector exactly
    # then. Stating the flag TRUE is byte-identical to leaving it unresolvable
    # (both keep the enter-only `then` arm), so a world with no On Leave pays
    # neither the code nor the table's BSS.
    defines["VM_TRIG_ENTER_ONLY"] = not any("function trigger_leave(" in text
                                            for _fn, text in sources)
    # TRIGGER FORCE RE-FIRE, the same rule: `vm.trigger` keeps a button-edge
    # re-fire of the standing trigger's enter script (the reference engine's PLATFORM
    # `INPUT_PLATFORM_FORCE_TRIGGER` scan) only when the generated rooms.mos
    # calls `trigger.set_force(`. Stating the flag TRUE is byte-identical to
    # leaving it unresolvable (both keep the plain `then` arms), so a world
    # with no forced trigger pays nothing.
    defines["VM_TRIG_NO_FORCE"] = not any("trigger.set_force(" in text
                                          for _fn, text in sources)
    # THE POINT-AND-CLICK CURSOR (W7j), the same rule once more: the generated
    # rooms.mos calls `player.set_cursor(` exactly when the world has a
    # `pointnclick` scene, and three modules then keep a per-frame arm for it -
    # vm.player's room flag, vm.canim's clip-state pin (a cross-bank call into
    # vm.player on EVERY animated frame, which is the expensive one) and
    # vm.entity's overlap probe. Stating the flag TRUE is byte-identical to
    # leaving it unresolvable (both keep the empty `then` arms), so a world
    # with no such scene pays nothing.
    #
    # NOT the handler itself: `update_pointnclick` is unreferenced in such a
    # program and the mos compiler drops an unreferenced function outright -
    # the same reason `update_platform` has no body at all in the shooter conversion's
    # generated C. This flag exists for the arms that live INSIDE functions
    # every program calls.
    defines["VM_NO_CURSOR"] = not any("player.set_cursor(" in text
                                      for _fn, text in sources)
    # THE LETTERBOX VIEW (`[scenes] letterbox`), the same rule: the generated
    # rooms module calls `core.set_view(` exactly when the project asks for a
    # room smaller than the screen to be shown centred, and vm.core then
    # anchors the box and the menu to the ROOM's bottom instead of the
    # screen's. Stating the flag TRUE keeps the screen-anchored arms, so a
    # project without the knob is byte-identical.
    defines["VM_NO_VIEW"] = not any("core.set_view(" in text
                                    for _fn, text in sources)
    # SAVE SLOTS (W7c). Unlike the flags above this one is derived from the
    # BLOB, not from a generated call, because the blob is where the answer is:
    # a project uses more than slot 0 exactly when its bytecode writes the
    # `save_slot` state or carries one of the two new opcodes. It folds the
    # latch, the second half of the save seam and vm.sram's slot offset
    # together, so a project that saves only to slot 0 keeps the single-blob
    # SRAM layout it has today and pays nothing for the other two.
    defines["VM_SAVE_SLOTS"] = bool(defines.get("VM_ST_SAVE_SLOT")
                                    or defines.get("VM_OP_DATA_CLEAR")
                                    or defines.get("VM_OP_DATA_PEEK"))
    return defines


def _wants_music_isr(sources, platform) -> bool:
    """Whether THIS build ticks vm.music from the VBL interrupt. Two
    conditions, both required:

    * the program wires the vm.music driver (`core.set_music_driver(music.play,
      ...)` in a generated glue.mos or a hand-written shell). A hUGEDriver
      project wires `music_huge.play` and never matches - its tick is already
      a 64 Hz timer ISR and its per-frame seam is empty;
    * the target console is in `platforms.MUSIC_ISR_CONSOLES` (the GB family +
      SMS/GG, which all expose GBDK's add_VBL chain). Lynx/PCE keep the
      main-loop display-frame catch-up.

    The flag folds `lib/vm/core.mos`'s per-frame catch-up arm and music_pump
    away (the VBL owns the tick; ticking in both places would double the
    tempo) and arms the busy latch around the driver's bytecode-side entry
    points, so a `music.play` from the main loop can never race the ISR.

    TWO TRAPS a naive text scan walks into, both found by a hUGE project's
    ROM changing bytes (it must not):

    * COMMENTS: vm.music's own module header documents the wiring line
      verbatim (the `extract_imports` quoted-import trap again), so each
      line is stripped at `--` before matching.
    * THE GLUE FORK: a hUGEDriver project's generated glue.mos carries BOTH
      wirings - `music_huge.play` under its GB-family arm and `music.play`
      under the SMS/GG/Lynx arm - and a text scan cannot see conditional
      compilation. So this reproduces the fork's own rule: on a GB-family
      target a `music_huge.play` wiring WINS (the vm.music arm is the one
      that folds away there), and on SMS/GG the vm.music arm is the live one.
    """
    import re
    from mosaik.platforms import (MUSIC_ISR_CONSOLES, PLATFORM_CAPS,
                                  canonical_platform)
    plat = canonical_platform(platform)
    if plat not in MUSIC_ISR_CONSOLES:
        return False
    wire = re.compile(r'core\.set_music_driver\(\s*music\.play')
    huge = re.compile(r'core\.set_music_driver\(\s*music_huge\.play')
    has_wire = has_huge = False
    for _fn, text in sources:
        for line in text.splitlines():
            code = line.split('--', 1)[0]
            if wire.search(code):
                has_wire = True
            if huge.search(code):
                has_huge = True
    if has_huge and PLATFORM_CAPS[plat].get('has_gb_regs'):
        return False        # the glue fork wires hUGEDriver on this target
    return has_wire


def _wants_music_subpat(sources) -> bool:
    """Whether THIS program wires vm.music's instrument SUBPATTERNS (the hUGE
    v6 per-instrument tables): a `music.set_subpatterns(` call in the
    generated glue, which `mosaik_vm.glue` emits only when an instrument plays
    one. Stated as `VM_MUSIC_SUBPAT`, which compiles the driver's table
    machinery at all; absent, every table arm folds away (byte-identical).

    Comments are stripped first - vm.music's own header names the wiring
    line, the trap `_wants_music_isr` documents. No per-platform fork is
    needed: the table ticks live inside vm.music's per-console branches (GB,
    Lynx, SMS/GG, PCE), so on the NES the define only keeps a two-pointer
    setter."""
    import re
    wire = re.compile(r'music\.set_subpatterns\(')
    for _fn, text in sources:
        for line in text.splitlines():
            if wire.search(line.split('--', 1)[0]):
                return True
    return False


def _songs_table(sources, name):
    """The values of the generated `songs` module's const array `name`, or
    None when the program has no songs module. Read off the text the compiler
    is about to compile, so it cannot be stale against the ROM."""
    import re
    arr = re.compile(r'const\s+%s\s*:\s*array\[u8,\s*\d+\]\s*=\s*\[([^\]]*)\]' % name)
    for _fn, text in sources:
        if not re.search(r'module\s+"songs"', text):
            continue
        m = arr.search(text)
        if m:
            return [int(v, 16 if v.startswith('0x') else 10)
                    for v in re.findall(r'0x[0-9A-Fa-f]+|\d+', m.group(1))]
    return None


def _wants_music_borrow(sources) -> bool:
    """Whether a song in THIS program plays the beep's own channel
    (`gb_all_voices`, VOICING bit0: the GB's pulse 2, music-only voicing's
    SMS/GG tone 0, Lynx Mikey A, PCE PSG 0). Stated as `VM_MUSIC_BORROW`, which
    makes vm.music leave that channel alone while `sound.busy()` (hUGEDriver's
    borrow) and, on the pooled consoles, gives the music that channel LAST;
    absent, every write is verbatim (byte-identical)."""
    flags = _songs_table(sources, 'FLAGS')
    return bool(flags) and any(v & 1 for v in flags)


def _wants_music_empty(sources) -> bool:
    """Whether a song in THIS program has a channel with no note at all
    (`songs.KIND_EMPTY` in its CHKIND table). Stated as `VM_MUSIC_EMPTY`, which
    makes vm.music give that channel no voice on the pooled consoles (the GB
    routes by kind and skips it for free); absent, byte-identical."""
    from mosaik_vm.songs import KIND_EMPTY
    kinds = _songs_table(sources, 'CHKIND')
    return bool(kinds) and KIND_EMPTY in kinds


# --------------------------------------------------------------------------
# THE PC ENGINE BANK EDGE. A HuCard boots with physical bank 0 at $E000, so a
# 32 KB image is rotated: logical $8000 / $A000 / $C000 / $E000 are physical
# banks 1 / 2 / 3 / 0. Every logical edge but ONE joins two physically
# adjacent banks; $DFFF/$E000 joins bank 3 to bank 0. mednafen_pce_fast (the
# studio's PCE preview and the suite's emulator) fetches an instruction's
# operand bytes from the page the OPCODE is on, so an instruction that starts
# before $E000 and ends at or after it reads its operand from past bank 3:
# `lda abs,y` at $DFFE loads from $FFxx, measured (tests/pce_bank_edge_test.py,
# which pins the probe). It cost vm.music's PCE envelope a whole debugging
# session: the code was correct, its compare simply straddled the edge.
# The build therefore links once, decodes the code at the edge, and, only if an
# instruction spans it, relinks with 1..7 pad bytes in front of CODE so the
# edge falls between two instructions. An image with no straddle is unchanged.
# --------------------------------------------------------------------------
PCE_SPLIT_EDGE = 0xE000

#: HuC6280 instruction length by opcode, derived from cc65's own disassembler
#: (`da65 --cpu huc6280`; an undefined opcode counts 1). The test re-derives it.
PCE_OPLEN = bytes([
    1, 2, 1, 2, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    2, 2, 2, 2, 2, 2, 2, 2, 1, 3, 1, 1, 3, 3, 3, 3,
    3, 2, 1, 2, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    2, 2, 2, 1, 2, 2, 2, 2, 1, 3, 1, 1, 3, 3, 3, 3,
    1, 2, 1, 2, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    2, 2, 2, 2, 1, 2, 2, 2, 1, 3, 1, 1, 1, 3, 3, 3,
    1, 2, 1, 1, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    2, 2, 2, 7, 2, 2, 2, 2, 1, 3, 1, 1, 3, 3, 3, 3,
    2, 2, 1, 3, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    2, 2, 2, 4, 2, 2, 2, 2, 1, 3, 1, 1, 3, 3, 3, 3,
    2, 2, 2, 3, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    2, 2, 2, 4, 2, 2, 2, 2, 1, 3, 1, 1, 3, 3, 3, 3,
    2, 2, 1, 7, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    2, 2, 2, 7, 1, 2, 2, 2, 1, 3, 1, 1, 1, 3, 3, 3,
    2, 2, 1, 7, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    2, 2, 2, 7, 1, 2, 2, 2, 1, 3, 1, 1, 1, 3, 3, 3,
])

#: The executable segments of cc65's pce.cfg (everything else is data).
PCE_CODE_SEGMENTS = ("CODE", "LOWCODE", "ONCE", "STARTUP")


def pce_map_segments(map_text: str) -> Dict[str, tuple]:
    """{segment: (start, end)} from an ld65 map's "Segment list" (end inclusive)."""
    out, on = {}, False
    for line in map_text.splitlines():
        if line.startswith("Segment list"):
            on = True
            continue
        if on:
            p = line.split()
            if len(p) >= 4 and re.fullmatch(r"[0-9A-Fa-f]{6}", p[1] or "") \
                    and re.fullmatch(r"[0-9A-Fa-f]{6}", p[2]):
                out[p[0]] = (int(p[1], 16), int(p[2], 16))
            elif out and not line.strip():
                break
    return out


def pce_label_addresses(lbl_text: str) -> List[int]:
    """Every address a VICE label file (`cl65 -Ln`) names, sorted."""
    out = set()
    for line in lbl_text.splitlines():
        p = line.split()
        if len(p) >= 3 and p[0] == "al":
            out.add(int(p[1], 16))
    return sorted(out)


def pce_edge_instructions(image: bytes, segments: Dict[str, tuple],
                          labels: List[int]) -> List[tuple]:
    """The (address, length) of every code instruction from a sync label up to
    PCE_SPLIT_EDGE, for the code segment that covers the edge; [] when the edge
    is not inside code. `image` is the LINEAR linker output (offset = address -
    $8000). Decoding starts at the last label at least 8 bytes before the edge
    (a label in code is an instruction start), so a pad of up to 7 bytes can be
    priced from the same list."""
    for name in PCE_CODE_SEGMENTS:
        lo, hi = segments.get(name, (0, -1))
        if not (lo < PCE_SPLIT_EDGE <= hi):
            continue
        sync = max([a for a in labels if lo <= a <= PCE_SPLIT_EDGE - 8] or [lo])
        pc, out = sync, []
        while pc < PCE_SPLIT_EDGE:
            n = PCE_OPLEN[image[pc - 0x8000]]
            out.append((pc, n))
            pc += n
        return out
    return []


def pce_edge_pad(instructions: List[tuple]) -> Optional[int]:
    """The fewest pad bytes (0..7) in front of the code that leave no
    instruction spanning PCE_SPLIT_EDGE; None if none does."""
    for pad in range(8):
        if not any(a + pad < PCE_SPLIT_EDGE < a + pad + n for a, n in instructions):
            return pad
    return None


def pce_cfg_with_edgepad(cfg: str) -> Optional[str]:
    """`cfg` with an EDGEPAD segment placed right before CODE (so it shifts
    CODE and everything linked after it), or None if CODE is not found."""
    anchor = "    CODE:     load = ROM,"
    i = cfg.find(anchor)
    if i < 0:
        return None
    return (cfg[:i] + "    EDGEPAD:  load = ROM,             type = ro,  optional = yes;\n"
            + cfg[i:])


class MosaikBuilder:
    """Main mosaik build system."""

    def __init__(self, config_path: Optional[str] = None):
        self.config = BuildConfig(config_path)
        self.gbdk = GBDKInterface()
        self.cc65 = Cc65Interface()
        self.source_manager = SourceManager(self.config)
        self.compiler = MosaikCompiler()

    def build(self, target: str = None, platform: str = None, debug: bool = False,
              asset_files: List[str] = None, all_platforms: bool = False) -> bool:
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
            return self.build_single_file(target, platform, debug, asset_files,
                                          all_platforms)

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
                          asset_files: List[str] = None,
                          all_platforms: bool = False) -> bool:
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
        # non-stdlib imports (resolved next to the importing file, then under
        # the library search roots -- so `import "engine.camera"` works without
        # vendoring lib/ into the source folder).
        lib_roots = self._lib_search_roots(
            os.path.dirname(os.path.abspath(self.config.config_path)))
        source_files = self._resolve_import_closure(source_file, lib_roots)
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
        if all_platforms:
            target_platforms = list(PLATFORM_TARGETS.keys())
        elif platform:
            target_platforms = [platform]
        else:
            target_platforms = self.config.get_target_platforms()

        # PNG assets come from --asset flags in single-file mode (paths are
        # relative to the working directory). Conversion is per-target: the
        # sprite depth (2bpp / 4bpp) depends on the console (see _convert_assets).
        success = True
        for target_platform in target_platforms:
            print(f"Building for platform: {target_platform}")
            converted = self._convert_assets(asset_files or [],
                                              target_sprite_bpp(target_platform),
                                              obj_8x16=self._obj16_for(target_platform))
            if converted is None:
                success = False
                continue
            assets, asset_palettes, asset_palettes16, asset_sprites = converted
            if not self.build_target(source_files, target_platform, output_dir,
                                     base_name, debug, assets, asset_palettes,
                                     asset_palettes16, asset_sprites):
                success = False
        return success

    def _lib_search_roots(self, base_dir: str) -> List[str]:
        """Library search roots for resolving non-vendored framework imports.

        An `import "engine.camera"` that isn't found next to the importing file
        falls back to these roots (`<root>/game/camera.mos`), so the shared
        `lib/` modules can be used without copying them into the project.
        Roots, in priority order:

          1. the project's `[lib] paths` (relative to `base_dir`),
          2. the `MOSAIK_LIB` environment variable (an os.pathsep-separated
             list of roots),
          3. the default `lib/` folder next to this tool.

        Only existing directories are returned (deduplicated, order kept).
        """
        roots = []
        for p in self.config.get_lib_paths():
            roots.append(os.path.abspath(os.path.join(base_dir, p)))
        env = os.environ.get('MOSAIK_LIB')
        if env:
            for p in env.split(os.pathsep):
                if p.strip():
                    roots.append(os.path.abspath(p.strip()))
        here = os.path.dirname(os.path.abspath(__file__))
        roots.append(os.path.join(here, 'lib'))

        seen, out = set(), []
        for r in roots:
            if r not in seen and os.path.isdir(r):
                seen.add(r)
                out.append(r)
        return out

    def _resolve_import_closure(self, entry_files, lib_roots: List[str] = None
                                ) -> Optional[List[str]]:
        """Resolve non-stdlib imports to `.mos` files, following them transitively.

        `import "name"` either names a stdlib module (resolved by the
        compiler) or another source file. A source import is located
        relative to the importing file's folder first -- `name.mos`, with
        dots mapping to subfolders (`import "game.utils"` ->
        `game/utils.mos`, falling back to `game.utils.mos`) -- so a vendored
        copy always wins; failing that, it is looked up under each library
        search root in `lib_roots` (the shared `lib/` model). `entry_files`
        is one path or a list of seed files (whose imports are followed).
        Returns the full source list (seeds first, in order, then pulled-in
        modules) or None when an import cannot be found.
        """
        from mosaik import stdlib_module_names
        stdlib_names = stdlib_module_names()
        lib_roots = lib_roots or []
        if isinstance(entry_files, str):
            entry_files = [entry_files]

        ordered, seen, queue = [], set(), []
        for ef in entry_files:
            ap = os.path.abspath(ef)
            if ap not in seen:
                seen.add(ap)
                ordered.append(ap)
                queue.append(ap)
        while queue:
            current = queue.pop(0)
            base_dir = os.path.dirname(current)
            for name in self.source_manager.extract_imports(current):
                if name in stdlib_names:
                    continue
                candidates = []
                for root in [base_dir] + lib_roots:
                    for candidate in (os.path.join(root, *name.split('.')) + '.mos',
                                      os.path.join(root, name + '.mos')):
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

    def _console_asset_paths(self, asset_paths: List[str], platform: str) -> List[str]:
        """Per-console asset override: if a sibling ``<dir>/<platform>/<file>`` exists
        next to an asset, use it for that target instead.

        The override keeps the SAME filename, so ``asset_c_name`` derives the same C
        symbol -- the generated code references it unchanged; only the pixels/palette
        differ (e.g. a 16-colour Lynx sheet beside the 4-colour base). The escape
        hatch for art that deserves richer colour on one console (MosaiK Studio's
        Palette designer item 13e). A missing override just falls through to the base.
        """
        out = []
        for p in asset_paths:
            d, fn = os.path.split(p)
            cand = os.path.join(d, platform, fn)
            out.append(cand if os.path.isfile(cand) else p)
        return out

    def _obj16_for(self, platform: str) -> bool:
        """`[build] obj_8x16` EFFECTIVE for this target: the mode is hardware
        (the GB family's LCDC bit 2, the SMS/GG VDP R1 sprite-size bit), and
        the column-major tile reorder must only happen where the 8x16 fan
        reads it -- reordering a Lynx build's tiles would scramble every
        sprite there. The asset conversion, the compile and the codegen all
        gate on this one test (`platforms.obj16_effective`)."""
        return obj16_effective(platform, self.config.get_obj_8x16())

    def _bake_soft_flip(self, assets, project_dir: str, platform: str):
        """The SMS/GG SOFT FLIP under per-room sprite residency: on a console
        with no hardware sprite flip, append each `flip_left` kind's mirrored
        RIGHT cells to the END of that kind's own sheet data.

        Per-room residency uploads every kind's sheet at its own VRAM base and
        the clips frames are kind-RELATIVE, so a mirror has to travel INSIDE
        the kind's sheet: it then rides the ordinary upload (`upload_kind`, the
        far read from its data bank), and `<sheet>_tile_count` grows on these
        consoles only. `mosaik_anim.residency_flip_bake` is the one planner, so
        the generated clips (the SMS/GG frame indices) and rooms (the per-kind
        tile counts) agree with what is appended here. The mirror is made from
        the sheet's ENCODED tiles, so it is in the build's own tile layout
        (8x16 column-major included). Anything else - a flip-capable console, a
        boot-upload world (its bake lives in clips.mos), no flip_left - returns
        the assets untouched, byte for byte."""
        if PLATFORM_CAPS.get(canonical_platform(platform), {}).get(
                "has_sprite_flip", True):
            return assets
        studio = os.path.join(project_dir, "studio.toml")
        if not os.path.isfile(studio):
            return assets
        import toml
        import mosaik_anim
        from mosaik_assets import mirror_cell_tiles
        data = toml.load(studio)
        if (data.get("sprites", {}) or {}).get("residency") != "room":
            return assets
        anims = data.get("animations", {}) or {}
        kpngs = {k: os.path.join(project_dir, "assets", str(s) + ".png")
                 for k, s in (data.get("kind_sprites", {}) or {}).items()}
        plan = mosaik_anim.residency_flip_bake(
            anims, {k: p for k, p in kpngs.items() if os.path.isfile(p)})
        if not plan:
            return assets
        col_major = self._obj16_for(platform)
        out = []
        for name, tiles, bpp in assets:
            got = plan["by_stem"].get(name)
            if got and bpp == 2:
                extra = b"".join(mirror_cell_tiles(tiles, src, w, h, col_major)
                                 for src, w, h in got["cells"])
                print(f"  Soft flip: {name} +{len(extra) // 16} mirrored tiles "
                      f"(no hardware sprite flip on {platform})")
                tiles = bytes(tiles) + extra
            out.append((name, tiles, bpp))
        return out

    def _convert_assets(self, asset_paths: List[str], sprite_bpp: int = 2,
                        obj_8x16: bool = False):
        """Convert PNG assets to tile data for a target of depth `sprite_bpp`.

        2bpp targets get the universal GB 2bpp encoding (the historical
        default); a 4bpp target (Lynx) with a >4-colour indexed PNG gets the
        native packed-nibble 4bpp encoding plus the asset's 16-colour palette
        (`<name>_palette16`). Indexed PNGs with <=4 entries also carry their
        authored 4-colour palette (`<name>_palette`). With `obj_8x16` (this
        TARGET is a GB-family console in 8x16 OBJ mode) every manifested
        sub-sprite's tiles store COLUMN-major. Returns
        ([(name, data, bpp)], [(name, colors4)], [(name, colors16)]) or None.
        """
        if not asset_paths:
            return [], [], [], []
        try:
            assets = load_assets(asset_paths, sprite_bpp, obj_8x16=obj_8x16)
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
        # Remembered for the link step: `native.huge` renders the project's
        # songs.toml into hUGEDriver song data there, once the bank allocation
        # is known (single-file builds have no project and so no songs).
        self.project_root = project_dir
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

        # Pull in shared library modules for any imports not satisfied by the
        # project's own sources (resolved under the library search roots), so a
        # project can `import "engine.camera"` without vendoring lib/. The
        # project's files are the seeds; vendored copies still win (resolved
        # relative to the importer first). Whole-program tree-shaking keeps only
        # the modules actually used.
        lib_roots = self._lib_search_roots(project_dir)
        closure = self._resolve_import_closure(source_files, lib_roots)
        if closure is None:
            return False
        proj_abs = {os.path.abspath(f) for f in source_files}
        lib_files = [f for f in closure if f not in proj_abs]
        if lib_files:
            print(f"  Library modules ({len(lib_files)}):")
            for lf in lib_files:
                print(f"    + {lf}")
        source_files = source_files + lib_files

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
            converted = self._convert_assets(
                self._console_asset_paths(asset_paths, target_platform),
                target_sprite_bpp(target_platform),
                obj_8x16=self._obj16_for(target_platform))
            if converted is None:
                success = False
                continue
            assets, asset_palettes, asset_palettes16, asset_sprites = converted
            assets = self._bake_soft_flip(assets, project_dir, target_platform)
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

    def _convert_font(self):
        """`[assets] font` -> `(glyphs, widths)` for the glyph-buffer prelude,
        or `(None, None)` when the project sets no font.

        `widths` is None for a fixed-width sheet, which is every font shipped
        today; a VARIABLE-width sheet (one carrying the reference engine's marker colour)
        brings its whole ASCII 32..255 range and one advance width per glyph.
        Resolved relative to the project file, like `[assets] sprites`. A
        broken font is a HARD error -- silently shipping the built-in font
        against an authored one would be the classic quiet drop.
        """
        font = self.config.get_font_file()
        if not font:
            return None, None
        path = os.path.join(os.path.dirname(os.path.abspath(
            self.config.config_path)), font)
        import mosaik_assets
        try:
            return mosaik_assets.png_to_font_sheet(path)
        except Exception as exc:  # noqa: BLE001 -- name the file, then stop
            raise RuntimeError("[assets] font %r: %s" % (font, exc))

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
                # The text goes to the compiler UNCHANGED: a diagnostic's
                # `file:line` must be the file's own line. (A `# platform`
                # header used to be prepended here; the parser drops `#`
                # lines, so it did nothing but shift every line by 2.)
                sources.append((source_file, source_code))

            defines = _vm_dispatch_defines(sources)
            if _wants_music_isr(sources, platform):
                # vm.music ticks from the VBL interrupt on this target (6.6
                # stage 2), so a room load / box repaint can no longer silence
                # the song. Stated only when TRUE: compile_program supplies the
                # False default itself, keeping every non-user byte-identical.
                defines = dict(defines)
                defines['VM_MUSIC_ISR'] = True
            if _wants_music_subpat(sources):
                # Instrument SUBPATTERNS: stated only when wired, so every
                # project without one compiles vm.music as before.
                defines = dict(defines)
                defines['VM_MUSIC_SUBPAT'] = True
            if _wants_music_borrow(sources):
                # A song plays the GB's pulse 2: the beep borrows it while it
                # sounds. Stated only when TRUE (byte-identical otherwise).
                defines = dict(defines)
                defines['VM_MUSIC_BORROW'] = True
            if _wants_music_empty(sources):
                # A song channel that plays nothing takes no voice.
                defines = dict(defines)
                defines['VM_MUSIC_EMPTY'] = True
            if self.config.get_vm_quant() != 16:
                # Only stated when RAISING it: absent stays unresolvable, and an
                # unresolvable module-level condition keeps its `then` arm, which
                # is the spec-minimum 16. So a default build is byte-identical.
                defines = dict(defines)
                defines['VM_QUANT_MIN'] = False
            _flock = self.config.get_frame_lock()
            if _flock > 1:
                # Same pattern: stated only when opting IN. compile_program
                # supplies the OFF defaults itself, so a project that does not
                # lock keeps vm.core's frame tail verbatim (byte-identical).
                defines = dict(defines)
                defines['VM_FRAME_LOCK_ON'] = True
                defines['VM_FRAME_LOCK'] = _flock
            if self.config.get_proj_under_lock():
                # Same pattern: stated only when opting IN, so a project that
                # does not ask for the reference VM's lock semantics keeps vm.core's
                # frozen-flight arm verbatim (byte-identical).
                defines = dict(defines)
                defines['VM_PROJ_UNDER_LOCK'] = True
            if self.config.get_park_updates():
                # Same pattern: only stated when opting IN, so an absent flag
                # keeps vm.entity's always-run `then` arms byte-identical.
                defines = dict(defines)
                defines['VM_UPDATE_ALWAYS'] = False
            if self.config.get_move_lcd():
                # Same pattern once more: stated only when opting IN, so an
                # absent flag keeps vm.actor's one-step-per-call `then` arm and
                # every authored project is byte-identical. ON, a native auto-
                # move advances once per elapsed DISPLAY frame, which is GB
                # Studio's own unit and the only pacing that is right in a
                # room whose VM:LCD ratio is not the importer's assumed 2.
                defines = dict(defines)
                defines['VM_MOVE_PER_CALL'] = False
            scan = self.config.get_actor_scan()
            if scan != 1:
                # Same pattern again. The MASK rides along as an integer define
                # because the runtime's else arm declares `const SCAN_MASK =
                # VM_ACTOR_SCAN_MASK`; at the default that whole arm is folded
                # away, so the name is never referenced and needs no default.
                defines = dict(defines)
                defines['VM_ACTOR_SCAN_ALL'] = False
                defines['VM_ACTOR_SCAN_MASK'] = scan - 1
                defines['VM_ACTOR_SCAN_SHIFT'] = scan.bit_length() - 1
            if self.config.get_actor_deactivate():
                # Same pattern: stated only when opting IN, so an absent flag
                # keeps every live-list walk verbatim (byte-identical).
                defines = dict(defines)
                defines['VM_ACTOR_DEACT'] = True
            if self.config.get_bank_bytecode():
                # The generated scripts module registers the BANKED code
                # window (per-slice enter/leave) only when the blob is in a
                # bank; a resident blob takes the form without the pair.
                defines = dict(defines)
                defines['VM_CODE_BANKED'] = True
            pscan = self.config.get_proj_scan()
            if pscan != 1:
                # Same pattern once more (and the same reason the mask rides
                # along as an integer): at the default the whole spread arm is
                # folded away, so the name is never referenced.
                defines = dict(defines)
                defines['VM_PROJ_SCAN_ALL'] = False
                defines['VM_PROJ_SCAN_MASK'] = pscan - 1

            # `[build] actor_pool` / `trigger_pool` size the VM8 slot tables.
            # Only the keys the project actually set are passed; the compiler
            # supplies the default 8 for the rest, so this is byte-identical
            # unless a project opts in.
            pools = self.config.get_pool_defines()
            if pools:
                defines = dict(defines)
                defines.update(pools)

            # `[assets] font`: bitmaps, plus per-glyph advance widths when the
            # sheet is variable-width (None for every fixed-width one).
            font_rows, font_widths = self._convert_font()

            # Compile to C for this target console (drives `if platform ==`
            # conditional compilation and the platform-specific prelude).
            c_code = self.compiler.compile_program(sources, platform=platform,
                                                   defines=defines,
                                                   assets=assets,
                                                   asset_palettes=asset_palettes,
                                                   asset_palettes16=asset_palettes16,
                                                   asset_sprites=asset_sprites,
                                                   bkg_max_tiles=self.config.get_bkg_max_tiles(),
                                                   bkg_strip_w=self.config.get_bkg_strip_w(),
                                                   sprite_max_tiles=self.config.get_sprite_max_tiles(),
                                                   sprite_max_slots=self.config.get_sprite_max_slots(),
                                                   lynx_bkg16=self.config.get_lynx_bkg16(),
                                                   lynx_code_resident=self.config.get_lynx_code_resident(),
                                                   bank_bytecode=self.config.get_bank_bytecode(),
                                                   shake_exports=self.config.get_shake_exports(),
                                                   code_banks=self.config.get_code_banks(),
                                                   glyph_font=font_rows,
                                                   glyph_widths=font_widths,
                                                   obj_8x16=self._obj16_for(platform),
                                                   sms_start_button=self.config.get_sms_start_button())

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
            # Assembly units (cc65 ROM banking: the PC Engine trampoline +
            # mapper, which cc65's inline assembler cannot express). cl65
            # assembles a .s beside the C. None for a program that does not bank.
            for name, text in sorted(getattr(self.compiler.code_generator,
                                             'asm_units', {}).items()):
                asm_file = os.path.join(output_dir, f"{out_name}_{name}.s")
                with open(asm_file, 'w', encoding='utf-8') as f:
                    f.write(text)
                print(f"    ✅ Generated {asm_file} (assembly unit)")
                c_files.append(asm_file)
            return c_files

        except Exception as e:
            print(f"    ❌ Error: {e}")
            return None

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
            base_flags = target.get('cc65_flags')
            # Asset streaming (Lynx): when the codegen
            # moved const arrays into a cart archive, link two-pass + append it.
            archive = getattr(getattr(self.compiler, 'code_generator', None),
                              'streamed_archive', b"")
            stack = self.config.get_lynx_stack_size() if cc65_target == 'lynx' else None
            gen = getattr(self.compiler, 'code_generator', None)
            if cc65_target == 'lynx' and getattr(gen, 'cc65_max_bank', 0):
                # Code OVERLAYS (`[build] code_banks` on the Lynx): the cfg
                # gets the overlay window + one area per overlay, sized from
                # the compiled object (see cc65_bank).
                ovl = self._lynx_overlay_flags(c_files, rom_file, gen.cc65_max_bank)
                if ovl is None:
                    return False
                base_flags = list(base_flags or []) + ovl
            if cc65_target == 'lynx' and archive:
                success = self._link_lynx_streamed(c_files, rom_file, base_flags,
                                                   archive, debug, stack_size=stack)
            elif cc65_target == 'pce' and getattr(
                    getattr(self.compiler, 'code_generator', None),
                    'cc65_max_bank', 0):
                success = self._link_pce_banked(
                    c_files, rom_file, base_flags, debug,
                    self.compiler.code_generator.cc65_max_bank)
            elif cc65_target == 'pce' and self._pce_stock_cfg() is not None:
                success = self._link_pce_guarded(c_files, rom_file, list(base_flags or []),
                                                 debug, self._pce_stock_cfg(),
                                                 rom_file + '.cfg')
                if success:
                    self._fixup_pce_image(rom_file)
            else:
                success = self.cc65.link_target(c_files, rom_file, cc65_target,
                                                debug, base_flags, stack_size=stack)
                if success and cc65_target == 'pce':
                    self._fixup_pce_image(rom_file)
            if success:
                file_size = os.path.getsize(rom_file)
                print(f"    ✅ ROM created: {rom_file} ({file_size} bytes)")
                print(f"    📊 Compiled with cc65 (target {cc65_target})")
            return success

        # GBDK path: `lcc` compiles the generated C (home TU + any bank TUs)
        # straight to ROM, with the cartridge-geometry flags when needed.
        # native.huge: add the prebuilt hUGEDriver object + its header's include
        # path, ONLY for a program that imports it (so every other build's link
        # line is unchanged). The codegen has already refused the import off the
        # GB family, so reaching here means the target has the APU for it.
        #
        # This runs BEFORE the cart geometry is decided, and that ordering is
        # load-bearing: the song banks are allocated ABOVE everything the codegen
        # placed, so a cart sized without them can be too small to hold them.
        # Sizing first is how the 17-scene conversion linked fine until its map
        # data grew one bank, pushed the songs from 31 to 33, and failed with
        # makebin's generic "ROM is too large for number of banks specified" -
        # whose hints blame a big resident song, the wrong cause entirely.
        huge_objs, huge_flags, huge_banks = [], [], []
        if getattr(self.compiler.code_generator, 'native_huge_imported', False):
            inc, obj = hugedriver_paths(platform)
            if obj is None:
                print("    ❌ Error: native.huge needs the prebuilt hUGEDriver "
                      "object, which is missing from %s" % inc)
                return False
            huge_flags = ['-I%s' % inc]
            huge_objs = [obj]
            # The song data is rendered HERE, after compiling, because its ROM
            # banks have to start above whatever the codegen just allocated for
            # `[build] code_banks` and streamed data -- nothing generated earlier
            # could know that. Written beside the generated C, so it is a build
            # artifact and can never go stale against songs.toml.
            songs = self._generate_huge_songs(c_files)
            if songs is None:
                return False
            song_files, huge_banks = songs
            c_files = c_files + song_files
        try:
            extra_flags = self._gbdk_cart_flags(platform, huge_banks) + huge_flags
        except ValueError as e:
            print(f"    ❌ Error: {e}")
            return False
        # A banked build on a 0x4000-window console gets a linker map so the
        # resident image can be overlap-checked (see GBDK_BANK_WINDOW_BASE).
        check_window = (bool(self.compiler.code_generator.bank_units)
                        and canonical_platform(platform)
                        in GBDK_BANK_WINDOW_PLATFORMS)
        if check_window:
            extra_flags = extra_flags + ['-Wl-m']
        success = self.gbdk.compile_assembly(c_files + huge_objs, rom_file,
                                             platform, debug, extra_flags)
        if success and check_window:
            success = self._check_gbdk_bank_window(rom_file)

        if success:
            file_size = os.path.getsize(rom_file)
            print(f"    ✅ ROM created: {rom_file} ({file_size} bytes)")
            print(f"    📊 Compiled with {self.gbdk.version_info.get('type', 'GBDK-2020')}")

        return success

    def _generate_huge_songs(self, c_files: List[str]):
        """Render the project's `scripts/songs.toml` into hUGEDriver song data
        beside the generated C. Returns (new files, the ROM banks they occupy),
        either possibly empty, or None on a hard error. The caller must size the
        cart over those banks - they sit above every bank the codegen allocated.

        The FIRST BANK is the one after everything the codegen placed: banked
        function TUs (`bank_units`) and banked const data (`data_bank_syms`).
        Picking it here rather than in the generator is what keeps the two
        allocations from colliding without either knowing about the other."""
        g = self.compiler.code_generator
        used = set(getattr(g, 'bank_units', {}) or {})
        used |= set(getattr(g, 'data_bank_syms', {}) or {})
        used |= set(getattr(g, '_func_banks', ()) or ())
        first = (max(used) + 1) if used else 1
        root = getattr(self, 'project_root', None) or os.getcwd()
        out_dir = os.path.dirname(c_files[0]) if c_files else root
        # W7h: the `routines` table in each song descriptor is NULL unless the
        # blob ATTACHES one (`VM_OP_MUSIC_ROUTINE`), so a project with no music
        # routine keeps the descriptor it had, byte for byte - and so does the
        # thunk that would fill the queue (see `_emit_gbdk_huge`). Read off the
        # same dispatch defines the codegen was given, which is the one place
        # that knows what the blob contains.
        routines = bool((getattr(g, 'defines', None) or {}).get(
            'VM_OP_MUSIC_ROUTINE'))
        try:
            from mosaik_vm import huge as _huge
            paths, warns, banks = _huge.generate_huge_songs(
                root, out_dir, first_bank=first, routines=routines)
        except Exception as e:                      # a clear message, not a traceback
            print(f"    ❌ Error rendering hUGEDriver song data: {e}")
            return None
        for w in warns:
            print(f"    ⚠️  hUGEDriver songs: {w}")
        if paths:
            print("    🎵 hUGEDriver song data: %d file(s) in ROM banks %d-%d"
                  % (len(paths), min(banks), max(banks)))
        return paths, banks

    def _check_gbdk_bank_window(self, rom_file: str) -> bool:
        """Fail a banked GBDK link whose resident image crosses the bank window.

        sdcc places the boot INITIALIZER/GSINIT after _CODE/_HOME with no check
        against the switchable window at 0x4000, and makebin then writes bank
        1's content over the overflowing tail in the ROM file -- a SILENT boot
        corruption (black screen). Parse the map and error clearly instead.
        """
        map_file = os.path.splitext(rom_file)[0] + '.map'
        if not os.path.exists(map_file):
            print("    ⚠️ No linker map found; skipped the bank-window "
                  "overlap check (%s)" % map_file)
            return True
        with open(map_file, 'r', encoding='utf-8', errors='replace') as f:
            map_text = f.read()
        # A banked area larger than the 16 KB window is the same silent
        # corruption one bank up: makebin writes its tail over the next bank.
        over = gbdk_bank_overflows(map_text)
        if over:
            for name, size in over:
                print("    ❌ Error: banked area %s is %d bytes, %d past the "
                      "16 KB switchable window -- its tail silently "
                      "overwrites the next bank in the ROM file. Split the "
                      "bank's contents (fewer modules per code bank / "
                      "smaller banked arrays)."
                      % (name, size, size - 0x4000))
            try:
                os.remove(rom_file)
                print("    (removed the corrupt ROM: %s)" % rom_file)
            except OSError:
                pass
            return False
        end = gbdk_resident_end(map_text)
        if end <= GBDK_BANK_WINDOW_BASE:
            return True
        print("    ❌ Error: the resident image ends at 0x%04X, past 0x%04X "
              "where the switchable ROM bank maps -- bank 1 silently "
              "overwrites the overflowing %d bytes in the ROM file (a "
              "black-screen boot). Shrink the resident image: move code/data "
              "into bank(N), trim resident consts, or drop features."
              % (end, GBDK_BANK_WINDOW_BASE, end - GBDK_BANK_WINDOW_BASE))
        try:
            os.remove(rom_file)
            print("    (removed the corrupt ROM: %s)" % rom_file)
        except OSError:
            pass
        return False

    def _gbdk_cart_flags(self, platform: str, extra_banks=()) -> List[str]:
        """Cartridge-geometry makebin flags for a GBDK console.

        rom_size/ram_size (and `bank(N)` placements) translate to MBC5 +
        bank-count flags on consoles with banked-ROM support (PLATFORM_CAPS
        has_banking: the GB family minus the Mega Duck, whose cart mapper is
        unverified). On the other GBDK consoles a non-default rom_size/ram_size
        is reported as not applied rather than silently ignored. Raises
        ValueError for invalid sizes or an explicit rom_size too small for the
        banks the program uses.

        The cart is sized over EVERY bank something lands in, not just the code
        TUs: banked const DATA (`data_bank_syms`), individually banked functions
        (`_func_banks`) and the caller's `extra_banks` (hUGEDriver song data,
        allocated after compiling) all count. Sizing off `bank_units` alone
        under-sized the cart whenever one of the others reached higher, and
        makebin's only complaint is a generic "ROM is too large".
        """
        g = self.compiler.code_generator
        max_bank = max([0]
                       + list(extra_banks)
                       + list(getattr(g, 'bank_units', {}) or {})
                       + list(getattr(g, 'data_bank_syms', {}) or {})
                       + list(getattr(g, '_func_banks', ()) or ()))
        rom_banks = self.config.get_rom_banks()
        ram_banks = self.config.get_ram_banks()
        if platform_caps(platform)['has_banking']:
            # AUTO cart RAM: a save-using program (`platform.save`, incl. the VM8 save
            # pack) needs a battery-backed RAM bank in the cart header, or its writes don't
            # persist. Enable one when `ram_size` wasn't set, so SRAM save works with no
            # manual knob (ROM size already auto-sizes to the banks in gbdk_size_flags).
            if ram_banks == 0 and platform_caps(platform).get('has_save') \
                    and getattr(self.compiler.code_generator, 'save_imported', False):
                ram_banks = 1
                print("  💾 auto-enabled 8 KB cart RAM (platform.save is in use)")
            mapper = GBDK_MAPPER.get(platform, 'mbc5')
            return gbdk_size_flags(rom_banks, ram_banks, max_bank, mapper=mapper)
        if rom_banks not in (None, 2) or ram_banks:
            print("  ⚠️ rom_size/ram_size are not applied on '%s' "
                  "(banked carts are only wired up for the Game Boy family)"
                  % platform)
        return []

    def _link_lynx_streamed(self, c_files: List[str], rom_file: str,
                            base_flags: Optional[List[str]], archive: bytes,
                            debug: bool, stack_size: Optional[int] = None) -> bool:
        """Two-pass Lynx link for asset streaming.

        The codegen moved the streamed const arrays (per-scene maps) out of the
        resident image; their bytes are in `archive`. Link once to measure the
        boot image, compute the archive's cart byte offset (block-aligned), relink
        with that baked into the loader (`-D GBS_ARCHIVE_BASE=...`), then append
        the archive past the linked image. Cart-ROM offset X = .lnx file offset
        64 + X (the 64-byte header precedes the cart image); blocks are 1024 B.
        """
        LNX_HEADER, BLOCK = 64, 1024
        # Pass 1: link to measure the resident boot image.
        if not self.cc65.link_target(c_files, rom_file, 'lynx', debug, base_flags,
                                     stack_size=stack_size):
            return False
        cart_bytes = os.path.getsize(rom_file) - LNX_HEADER
        base = ((cart_bytes + BLOCK - 1) // BLOCK) * BLOCK   # archive on a block
        # Pass 2: relink with the archive's cart offset baked in. The define only
        # changes an immediate in the loader's lseek, so the image size is stable.
        flags = list(base_flags or []) + ['-D', 'GBS_ARCHIVE_BASE=%d' % base]
        if not self.cc65.link_target(c_files, rom_file, 'lynx', debug, flags,
                                     stack_size=stack_size):
            return False
        # Append the archive at cart offset `base` (= file offset 64 + base).
        with open(rom_file, 'rb') as f:
            data = bytearray(f.read())
        target_off = LNX_HEADER + base
        if len(data) < target_off:
            data.extend(b'\x00' * (target_off - len(data)))
        else:
            del data[target_off:]            # exact-equality no-op (sizes stable)
        data.extend(archive)
        while (len(data) - LNX_HEADER) % BLOCK != 0:   # whole final cart block
            data.append(0)
        with open(rom_file, 'wb') as f:
            f.write(data)
        print("    📦 Lynx asset archive: %d bytes streamed from cart "
              "(GBS_ARCHIVE_BASE = 0x%X)" % (len(archive), base))
        return True

    def lynx_overlay_cfg(self, max_bank: int) -> Optional[str]:
        """The ld65 config of a Lynx program with code OVERLAYS: the
        toolchain's own lynx.cfg with MAIN moved up above the overlay window
        ($0200 .. $0200 + __OVERLAYSIZE__; the stack top stays where crt0
        puts it) and one memory area per overlay at the window, written into
        the same .lnx after MAIN. None when the stock cfg is not the shape we
        extend."""
        path = os.path.join(self.cc65.cc65_path or '', 'cfg', 'lynx.cfg')
        try:
            with open(path, encoding='utf-8') as f:
                cfg = f.read()
        except OSError:
            return None
        main_old = 'start = $0200, size = $BE38 - __STACKSIZE__;'
        if main_old not in cfg or 'SYMBOLS {' not in cfg:
            return None
        cfg = cfg.replace(
            main_old,
            'start = $0200 + __OVERLAYSIZE__, '
            'size = $BE38 - __OVERLAYSIZE__ - __STACKSIZE__;', 1)
        cfg = cfg.replace(
            'SYMBOLS {',
            'SYMBOLS {\n    __OVERLAYSIZE__:      type = weak, value = $1000; # overlay window', 1)
        mem = "".join(
            '    OV%d:    file = %%O, define = yes, start = $0200, size = __OVERLAYSIZE__;\n'
            % k for k in range(1, max_bank + 1))
        seg = "".join(
            '    BK%d:      load = OV%d,    type = ro,  define = yes, optional = yes;\n'
            % (k, k) for k in range(1, max_bank + 1))
        mi = cfg.find('MEMORY {')
        mend = cfg.find('}', mi)
        cfg = cfg[:mend] + mem + cfg[mend:]
        si = cfg.find('SEGMENTS {')
        send = cfg.find('}', si)
        cfg = cfg[:send] + seg + cfg[send:]
        return ("# Generated by mosaik8: Lynx code overlays = the bundled lynx.cfg\n"
                "# + an overlay window at $0200 + one area per overlay (do not edit).\n"
                + cfg)

    def _lynx_overlay_flags(self, c_files: List[str], rom_file: str,
                            max_bank: int) -> Optional[List[str]]:
        """Write the overlay cfg and size the window: compile the C once to an
        object and take the largest BK<k> segment (od65), so MAIN gives up
        exactly the bytes the biggest overlay needs. Returns the extra cl65
        flags, or None on an error (reported)."""
        cfg = self.lynx_overlay_cfg(max_bank)
        if cfg is None:
            print("    ❌ Error: cannot extend the cc65 toolchain's lynx.cfg for overlays")
            return None
        cfg_file = rom_file + '.cfg'
        with open(cfg_file, 'w', encoding='utf-8') as f:
            f.write(cfg)
        cl65 = self.cc65._find_cl65(self.cc65.cc65_path)
        od65 = os.path.join(os.path.dirname(cl65), 'od65' + os.path.splitext(cl65)[1])
        sizes = {}
        for c in c_files:
            if not c.endswith('.c'):
                continue
            obj = rom_file + '.ovl.o'
            r = subprocess.run([cl65, '-t', 'lynx', '-O', '--static-locals', '-c',
                                '-o', obj, c], capture_output=True, text=True)
            if r.returncode:
                print("    ❌ Error compiling for the overlay sizes:\n" + r.stderr[-1500:])
                return None
            r = subprocess.run([od65, '--dump-segsize', obj], capture_output=True,
                               text=True)
            for m in re.finditer(r'^\s*BK(\d+):\s*(\d+)', r.stdout, re.M):
                sizes[int(m.group(1))] = sizes.get(int(m.group(1)), 0) + int(m.group(2))
            try:
                os.remove(obj)
            except OSError:
                pass
        window = max(sizes.values()) if sizes else 0
        if window <= 0:
            print("    ❌ Error: [build] code_banks placed no code in an overlay")
            return None
        print("    🧩 Lynx code overlays: %d, window %d B (largest), %s"
              % (len(sizes), window,
                 ", ".join("BK%d %d" % kv for kv in sorted(sizes.items()))))
        return ['-C', cfg_file, '-Wl', '-D__OVERLAYSIZE__=0x%04X' % window]

    #: One logical PC Engine ROM bank: the 16 KB window at $4000-$7FFF
    #: (MPR2 + MPR3, two physical 8 KB banks). See mosaik/codegen/cc65_bank.py.
    PCE_BANK_BYTES = 0x4000
    PCE_RESIDENT_BYTES = 0x8000

    def pce_banked_cfg(self, max_bank: int) -> Optional[str]:
        """The ld65 config of a banked HuCard: the toolchain's own pce.cfg
        (so the resident 32 KB image links exactly as it always has) plus one
        16 KB memory area per logical bank at $4000, each written to its own
        file (`<rom>.bk<k>`) that `_link_pce_banked` appends in order. None
        when the toolchain's pce.cfg is missing or not the shape we extend."""
        path = os.path.join(self.cc65.cc65_path or '', 'cfg', 'pce.cfg')
        try:
            with open(path, encoding='utf-8') as f:
                cfg = f.read()
        except OSError:
            return None
        mem = "".join(
            '    ROMBK%d: file = "%%O.bk%d", start = $4000, size = $4000, '
            'fill = yes, fillval = $FF;\n' % (k, k)
            for k in range(1, max_bank + 1))
        seg = "".join(
            '    BK%d:      load = ROMBK%d,          type = ro,  optional = yes;\n'
            % (k, k) for k in range(1, max_bank + 1))
        mi, si = cfg.find('MEMORY {'), cfg.find('SEGMENTS {')
        if mi < 0 or si < 0:
            return None
        mend = cfg.find('}', mi)
        cfg = cfg[:mend] + mem + cfg[mend:]
        si = cfg.find('SEGMENTS {')
        send = cfg.find('}', si)
        cfg = cfg[:send] + seg + cfg[send:]
        return ("# Generated by mosaik8: a banked PC Engine HuCard = the bundled\n"
                "# pce.cfg + one 16 KB area per logical ROM bank (do not edit).\n"
                + cfg)

    def _link_pce_banked(self, c_files: List[str], rom_file: str,
                         base_flags: Optional[List[str]], debug: bool,
                         max_bank: int) -> bool:
        """Link a PC Engine program that banks ROM (see cc65_bank).

        The resident image is today's 32 KB HuCard ($8000-$FFFF, rotated so
        the vector bank is file bank 0, exactly as `_fixup_pce_image` does).
        Logical bank k (16 KB, mapped at $4000 by MPR2 + MPR3) is physical
        banks 2k + 2 and 2k + 3, i.e. file offset 32 KB + (k - 1) * 16 KB: the
        banks are appended in order behind the rotated image, and the whole
        file is padded with $FF to the next power of two (a HuCard maps a file
        of that size without mirroring surprises)."""
        cfg = self.pce_banked_cfg(max_bank)
        if cfg is None:
            print("    ❌ Error: cannot extend the cc65 toolchain's pce.cfg for ROM banking")
            return False
        cfg_file = rom_file + '.cfg'
        with open(cfg_file, 'w', encoding='utf-8') as f:
            f.write(cfg)
        flags = list(base_flags or []) + ['-C', cfg_file]
        bank_files = [rom_file + '.bk%d' % k for k in range(1, max_bank + 1)]
        for bf in bank_files:
            if os.path.exists(bf):
                os.remove(bf)
        if not self._link_pce_guarded(c_files, rom_file, flags, debug, cfg, cfg_file):
            return False
        with open(rom_file, 'rb') as f:
            main = f.read()
        if len(main) != self.PCE_RESIDENT_BYTES:
            print("    ❌ Error: the resident PC Engine image is %d bytes, expected %d"
                  % (len(main), self.PCE_RESIDENT_BYTES))
            return False
        image = bytearray(main[-0x2000:] + main[:-0x2000])
        for k, bf in enumerate(bank_files, 1):
            data = b''
            if os.path.exists(bf):
                with open(bf, 'rb') as f:
                    data = f.read()
                os.remove(bf)
            if len(data) > self.PCE_BANK_BYTES:
                print("    ❌ Error: ROM bank %d is %d bytes, past the 16 KB window"
                      % (k, len(data)))
                return False
            image += data + bytes([0xFF]) * (self.PCE_BANK_BYTES - len(data))
        size = self.PCE_RESIDENT_BYTES
        while size < len(image):
            size *= 2
        image += bytes([0xFF]) * (size - len(image))
        with open(rom_file, 'wb') as f:
            f.write(image)
        print("    🗂️  PC Engine ROM banking: %d bank(s) of 16 KB at $4000, "
              "%d KB HuCard" % (max_bank, size // 1024))
        return True

    def _link_pce_guarded(self, c_files: List[str], rom_file: str,
                          flags: List[str], debug: bool, cfg: str,
                          cfg_file: str) -> bool:
        """Link a PC Engine image (LINEAR, before the boot-bank rotation) so no
        instruction spans the $DFFF/$E000 bank edge (see PCE_SPLIT_EDGE).

        `cfg` is the linker config the image links with and `cfg_file` where
        it lives; `flags` already name it with -C or, for the plain 32 KB
        image, use the toolchain's default (which `cfg` then is). The first
        link is the one the program always had; only when the decoded code at
        the edge straddles it is the image relinked, with EDGEPAD (1..7 bytes
        of $FF) in front of CODE."""
        lbl, mp = rom_file + '.lbl', rom_file + '.map'
        probe = ['-m', mp] + ([] if debug else ['-Ln', lbl])
        if not self.cc65.link_target(c_files, rom_file, 'pce', debug, list(flags) + probe):
            return False

        def instructions():
            try:
                with open(mp, encoding='utf-8', errors='replace') as f:
                    segs = pce_map_segments(f.read())
                with open(lbl, encoding='utf-8', errors='replace') as f:
                    labels = pce_label_addresses(f.read())
                with open(rom_file, 'rb') as f:
                    image = f.read()
            except OSError:
                return []
            if len(image) != self.PCE_RESIDENT_BYTES:
                return []
            return pce_edge_instructions(image, segs, labels)

        def tidy(pad_s=None):
            for p in ([mp] + ([] if debug else [lbl]) + ([pad_s] if pad_s else [])):
                if os.path.exists(p):
                    os.remove(p)

        pad = pce_edge_pad(instructions())
        if pad == 0:
            tidy()
            return True
        if pad is None:
            print("    ❌ Error: no pad of 1..7 bytes clears the PC Engine $DFFF/$E000 bank edge")
            tidy()
            return False
        padded = pce_cfg_with_edgepad(cfg)
        if padded is None:
            print("    ❌ Error: cannot place the PC Engine bank-edge pad in the linker config")
            tidy()
            return False
        with open(cfg_file, 'w', encoding='utf-8') as f:
            f.write(padded)
        pad_s = rom_file + '.edgepad.s'
        with open(pad_s, 'w', encoding='utf-8') as f:
            f.write('; GENERATED by mosaik8: the PC Engine bank-edge pad (see PCE_SPLIT_EDGE)\n'
                    '        .segment "EDGEPAD"\n        .res %d, $FF\n' % pad)
        relink = [x for i, x in enumerate(flags)
                  if not (x == '-C' or (i and flags[i - 1] == '-C'))] + ['-C', cfg_file]
        if not self.cc65.link_target(list(c_files) + [pad_s], rom_file, 'pce', debug,
                                     relink + probe):
            tidy(pad_s)
            return False
        left = [a for a, n in instructions() if a < PCE_SPLIT_EDGE < a + n]
        tidy(pad_s)
        if left:
            print("    ❌ Error: an instruction at $%04X still spans the PC Engine "
                  "$DFFF/$E000 bank edge after padding" % left[0])
            return False
        print("    🧭 PC Engine: %d pad byte(s) before CODE so no instruction spans the "
              "$DFFF/$E000 bank edge" % pad)
        return True

    def _pce_stock_cfg(self) -> Optional[str]:
        path = os.path.join(self.cc65.cc65_path or '', 'cfg', 'pce.cfg')
        try:
            with open(path, encoding='utf-8') as f:
                return f.read()
        except OSError:
            return None

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
