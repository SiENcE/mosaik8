"""MosaiK8 build targets + cartridge geometry (split out of mosaik8.py)."""
import os
import re
import sys
from typing import Dict, List, Optional, Any

try:
    import toml
except ImportError:
    print("Error: toml package required. Install with: pip install toml")
    sys.exit(1)

from mosaik import PLATFORM_CAPS, platform_caps
from mosaik.platforms import canonical_platform, BKG_4BPP_ENGINE


# Consoles whose sprite engine renders the native 4bpp (16-colour) tier today.
# A 4bpp-capable console NOT listed here falls back to the 2bpp grey tier
# (generalized-with-limits) until its engine gains a 4bpp path. The Lynx (Suzy
# literal sprites) and the PC Engine (VDC 4bpp planar patterns + a 16-colour VCE
# sprite palette) both consume the packed-nibble 4bpp asset -- review 2026-07-12
# item 5.1, "exploit each platform". SMS/GG stay 2bpp (their VDP 4bpp is planar
# and has no mosaik-emitted native-tile path yet).
SPRITE_4BPP_ENGINE = {'lynx', 'pce'}


def target_sprite_bpp(platform: str) -> int:
    """Sprite source depth to encode assets at for this target (2 or 4)."""
    cp = canonical_platform(platform)
    if cp in SPRITE_4BPP_ENGINE:
        return platform_caps(cp).get('sprite_bpp', 2)
    return 2


# The BACKGROUND 4bpp engine set lives in mosaik.platforms (the shared layer)
# because the scene transpiler reads it too; re-exported here beside its sprite
# sibling. See mosaik.platforms.BKG_4BPP_ENGINE.
def target_bkg_bpp(platform: str) -> int:
    """Background tileset source depth to encode at for this target (2 or 4)."""
    cp = canonical_platform(platform)
    if cp in BKG_4BPP_ENGINE:
        return platform_caps(cp).get('bkg_bpp', 2)
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


# Per-console ROM mapper for banked carts -- decides which cart-header bytes the
# makebin pass emits. The GB family uses an MBC5 (a GB cart-type byte at 0x147).
# Every other banking console's mapper is set up by its crt0 / auto-selected by
# makebin from the bank count, so the ROM only needs SIZING (`-Wm-yo<banks>`, no
# GB cart-type byte): the Sega mapper (SMS/Game Gear, `MAP_FRAME*`) and the NES
# UNROM mapper (iNES mapper 30, `_switch_prg0`; makebin auto-sets the iNES header
# from the bank count). Mega Duck is not banking-enabled yet (its cart header is
# unverified).
GBDK_MAPPER = {
    'gameboy': 'mbc5', 'gameboy_color': 'mbc5', 'analogue_pocket': 'mbc5',
    'sms': 'sega', 'gamegear': 'sega',
    'nes': 'unrom',
}

# The switchable-bank window on the sm83/z80 GBDK consoles starts at 0x4000:
# the GB-family MBC5 maps ROMX there, the SMS/Game Gear Sega mapper maps frame
# 1 there, and makebin lays bank N at ROM-file offset N*0x4000. sdcc's linker
# does NOT stop a BANKED build's resident image (_CODE/_HOME/_INITIALIZER/
# _GSINIT/_GSFINAL) from growing past 0x4000 -- bank 1 then silently OVERWRITES
# the overflowing tail in the ROM file (and every switched bank aliases it at
# runtime), so the ROM boots to a black screen with no diagnostic (bigworld-
# paint: adding the graphics.palette prelude pushed the 60-byte boot
# INITIALIZER to 0x4101 and bank 1 clobbered it -- the "graphics.palette blanks
# the SMS" mystery). link_rom parses the
# linker map after a banked link and fails loudly instead. (The NES is laid out
# differently -- its resident code lives in the FIXED top bank at 0x8000+ under
# its own linker script -- so the check covers only the 0x4000-window consoles.)
GBDK_BANK_WINDOW_BASE = 0x4000
GBDK_BANK_WINDOW_PLATFORMS = ('gameboy', 'gameboy_color', 'analogue_pocket',
                              'megaduck', 'sms', 'gamegear')

# hUGEDriver (`native.huge`) ships PREBUILT under vendor/hugedriver -- checked in,
# not fetched, so a project builds offline with no RGBDS. Two builds: the Mega Duck
# remaps the APU registers and needs its own. See that directory's README for the
# header/object matching rule, which is the trap here.
HUGEDRIVER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'vendor', 'hugedriver')
HUGEDRIVER_OBJS = {'megaduck': 'hUGEDriver_duck.o'}
HUGEDRIVER_DEFAULT_OBJ = 'hUGEDriver_gb.o'


def hugedriver_paths(platform: str):
    """(include_dir, object_path) for hUGEDriver on `platform`; object is None if
    the vendored build is missing."""
    obj = os.path.join(HUGEDRIVER_DIR,
                       HUGEDRIVER_OBJS.get(canonical_platform(platform),
                                           HUGEDRIVER_DEFAULT_OBJ))
    return HUGEDRIVER_DIR, (obj if os.path.isfile(obj) else None)


def gbdk_resident_end(map_text: str) -> int:
    """End address of the resident (bank-0) ROM image in an sdldgb/sdldz80
    linker map: max(addr + size) over the areas placed below 0x8000 with a
    zero bank field. Banked areas link at bank<<16 | 0x4000 and RAM areas at
    0xC000+, so both fall outside the filter."""
    end = 0
    for m in re.finditer(r'^\S+\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})\s+=',
                         map_text, re.M):
        addr, size = int(m.group(1), 16), int(m.group(2), 16)
        if size and addr < 0x8000:
            end = max(end, addr + size)
    return end


def gbdk_bank_overflows(map_text: str) -> List[tuple]:
    """Banked areas larger than the 16 KB switchable window, from an
    sdldgb/sdldz80 linker map: [(area_name, size), ...]. sdcc links a
    `_CODE_N` area at bank<<16 | 0x4000 and never checks it against the
    window, and makebin then writes the overflowing tail over the NEXT bank
    in the ROM file -- the same silent corruption class as the resident
    image crossing 0x4000, one bank up. (Found the hard way: ~20 KB of
    banked engine code in one bank passed the resident check and hung the
    console inside the first room load.)"""
    over = []
    for m in re.finditer(r'^(\S+)\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})\s+=',
                         map_text, re.M):
        name, addr, size = m.group(1), int(m.group(2), 16), int(m.group(3), 16)
        if addr >= 0x10000 and (addr & 0xFFFF) == GBDK_BANK_WINDOW_BASE \
                and size > 0x4000:
            over.append((name, size))
    return over


def gbdk_size_flags(rom_banks: Optional[int], ram_banks: int,
                    max_bank_used: int = 0, mapper: str = 'mbc5') -> List[str]:
    """makebin flags (-Wm-yt/-yo/-ya) for the requested cart geometry.

    `rom_banks` is the explicit `rom_size` in 16 KB banks (None = auto-size
    to the highest `bank(N)`/banked-data bank used, minimum the default 2).
    `mapper` selects the per-console cart header:

    - **mbc5** (GB family): an MBC (MBC5; or MBC5+RAM+BATTERY when cart RAM is
      requested) is emitted only when the cart actually needs one -- banked code/
      data, more than two ROM banks, or cart RAM -- so plain 32 KB builds keep
      producing byte-identical ROMs with no extra flags.
    - **sega** (SMS / Game Gear) / **unrom** (NES): the mapper is set up by the
      crt0 / auto-selected by makebin from the bank count, so there is NO GB
      cart-type byte; the ROM just needs to be sized to the bank count
      (`-Wm-yo<banks>`).

    Raises ValueError when an explicit rom_size cannot hold the highest bank the
    program places code/data in (config honesty: no silent resizing).
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
    if mapper != 'mbc5':
        # Sega / NES UNROM / future non-MBC mappers: no GB cart-type byte; the
        # mapper is set up by the crt0 or auto-selected from the bank count, so
        # the ROM just needs sizing to the banks.
        if rom_banks != 2:
            flags.append('-Wm-yo%d' % rom_banks)
        return flags
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
