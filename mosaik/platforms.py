"""Per-console capability registry and platform-name helpers."""


PLATFORM_ALIASES = {
    'gameboy': 'gameboy', 'gb': 'gameboy', 'dmg': 'gameboy',
    'gameboy_color': 'gameboy_color', 'gbc': 'gameboy_color',
    'cgb': 'gameboy_color', 'color': 'gameboy_color',
    'analogue_pocket': 'analogue_pocket', 'pocket': 'analogue_pocket',
    'ap': 'analogue_pocket', 'analogue': 'analogue_pocket',
    'megaduck': 'megaduck', 'mega_duck': 'megaduck', 'duck': 'megaduck',
    'sms': 'sms', 'master_system': 'sms', 'sega_master_system': 'sms',
    'gamegear': 'gamegear', 'game_gear': 'gamegear', 'gg': 'gamegear',
    'nes': 'nes', 'famicom': 'nes',
    # cc65 consoles (non-GBDK backend).
    'lynx': 'lynx', 'atari_lynx': 'lynx', 'atarilynx': 'lynx',
    'pce': 'pce', 'pc_engine': 'pce', 'pcengine': 'pce',
    'turbografx': 'pce', 'turbografx16': 'pce', 'tg16': 'pce',
}


# Per-console capability registry — the single source of truth for what each
# target console can do. Everything else derives from it: the backend choice
# (`framework`), which stdlib calls are available (and which raise a clear
# "not supported on target" compile error in _gen_call), which prelude blocks
# are emitted (window helpers, GB hardware-register #defines), and the
# sample-build matrix in tests/run_all.py. Keys must match mosaik8.py's
# PLATFORM_TARGETS (machine-checked there at import).
#
# - framework:    'gbdk' (emit GBDK C, link with lcc) or 'cc65' (cl65).
# - has_sprites:  graphics.sprite + the sprite-visibility video toggles.
# - has_bkg:      graphics.bkg (scrollable background tilemap).
# - has_window:   graphics.window + video.show/hide_window. Only the Game Boy
#   family has a real window layer; GBDK's SMS/GG SHOW_WIN macros are no-ops
#   and the NES port has none at all, so it is honest-off outside GB.
# - has_draw:     graphics.draw (TGI pixel/line/bar primitives; Lynx only).
# - has_gb_regs:  the Game Boy hardware-register constants (REG_DIV, REG_BGP,
#   ...) name real registers. Off elsewhere so `hw.write(REG_BGP, ...)` is a
#   clear compile error instead of a poke at a meaningless address.
# - has_sound:    platform.sound (sound.beep/sound.stop, one square-wave
#   channel). True everywhere today -- every supported console has a tone
#   generator (GB-family APU, SMS/GG PSG, NES APU, Lynx Mikey, PCE PSG) --
#   but kept in the registry so a future tone-less console stays honest.
_GB_FAMILY = {'framework': 'gbdk', 'has_sprites': True, 'has_bkg': True,
              'has_window': True, 'has_draw': False, 'has_gb_regs': True,
              'has_sound': True}
PLATFORM_CAPS = {
    'gameboy':         dict(_GB_FAMILY),
    'gameboy_color':   dict(_GB_FAMILY),
    'analogue_pocket': dict(_GB_FAMILY),
    'megaduck':        dict(_GB_FAMILY),
    'sms':             {'framework': 'gbdk', 'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False,
                        'has_sound': True},
    'gamegear':        {'framework': 'gbdk', 'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False,
                        'has_sound': True},
    'nes':             {'framework': 'gbdk', 'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False,
                        'has_sound': True},
    'lynx':            {'framework': 'cc65', 'has_sprites': True, 'has_bkg': False,
                        'has_window': False, 'has_draw': True, 'has_gb_regs': False,
                        'has_sound': True},
    'pce':             {'framework': 'cc65', 'has_sprites': True, 'has_bkg': False,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False,
                        'has_sound': True},
}


# Which compiler backend / SDK each console targets (derived from the caps
# registry). GBDK consoles emit GBDK C (linked by `lcc`); cc65 consoles emit
# cc65 C (linked by `cl65`). Adding a new console is a PLATFORM_CAPS entry plus
# a target descriptor in mosaik8.py's PLATFORM_TARGETS.
PLATFORM_FRAMEWORK = {name: caps['framework']
                      for name, caps in PLATFORM_CAPS.items()}


def canonical_platform(name) -> str:
    """Normalise a platform name/alias to its canonical form (default gameboy)."""
    if not name:
        return 'gameboy'
    key = str(name).strip().lower()
    return PLATFORM_ALIASES.get(key, key)


def framework_for_platform(name) -> str:
    """Return the codegen backend ('gbdk' or 'cc65') for a console."""
    return PLATFORM_FRAMEWORK.get(canonical_platform(name), 'gbdk')


def platform_caps(name) -> dict:
    """Return the capability entry for a console (default: gameboy)."""
    return PLATFORM_CAPS.get(canonical_platform(name), PLATFORM_CAPS['gameboy'])
