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
# - has_bkg:      graphics.bkg (scrollable background tilemap). True on every
#   console: GBDK consoles have a hardware tilemap layer; the PCE maps it to
#   the VDC BAT + BXR/BYR scroll registers; the Lynx (no tilemap hardware)
#   emulates it by compositing the 32x32 map into one large Suzy background
#   sprite re-blitted with wrapped offsets each present (the classic Lynx
#   big-background-sprite technique).
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
# - has_banking:  `bank(N)` function placement compiles to real banked-ROM
#   code (MBC5 cart + sdcc __banked far calls; see docs/banking-plan.md).
#   True on the Game Boy family except the Mega Duck (its cart mapper is
#   unverified); on every other console the annotation is accepted but
#   ignored, so one source with banked GB code still builds everywhere.
# - has_color:    programmable RGB colors (graphics.palette's palette.rgb /
#   set_bkg / set_sprite). The calls exist on *every* console -- on the 4-grey
#   machines (DMG, Mega Duck) colors quantize to shades and apply via
#   BGP/OBP0/OBP1, exactly what real GBC games do on a DMG -- so this flag is
#   documentation + prelude selection, not call gating.
# - bkg_palettes / spr_palettes: usable 4-color palette slots per layer
#   (graphics.palette slot arguments; out-of-range slots are masked or
#   ignored at run time). Slot 0 is the portable guarantee.
# - has_tile_palettes: bkg.set_palette (per-tile background palette
#   selection: GBC attribute map, PCE BAT bits, NES attribute table). Off
#   where the hardware has no per-tile palette in our model (DMG/Duck one BG
#   palette, SMS/GG one BG palette in CRAM, Lynx single-penpal composite) --
#   calling it there is a clear compile error.
# - sprite_bpp: the native sprite colour depth -- 2 (4 colours, the Game Boy
#   model) on the GB family / SMS / Game Gear / NES, 4 (16 colours) on the
#   Atari Lynx and PC Engine. The asset pipeline encodes sprite tiles at the
#   target's depth (a >4-colour indexed PNG becomes 4bpp on a sprite_bpp==4
#   console and is luma-quantized to the 2bpp grey ramp elsewhere -- the
#   generalized-with-limits fallback), and the cc65 Lynx engine widens its
#   literal sprite rows + Mikey pen map to 4bpp when fed 4bpp data. 2bpp
#   programs (hand-authored tiles, <=4-colour assets) are unaffected.
# - max_metasprite_tiles: largest metasprite (graphics.sprite's
#   sprite.set_meta, a W*H block of 8x8 tiles moved as one unit) the console's
#   sprite model comfortably draws. On the Game Boy family it is bounded by
#   the OAM budget (40 objects, 10 per scanline); the Lynx/PCE composite the
#   block into one hardware sprite so they are far more generous. The value
#   documents the ceiling and drives a compile-time warning -- it is not a
#   hard cap (oversized metasprites still emit, they just may drop objects on
#   GB-family scanlines).
_GB_FAMILY = {'framework': 'gbdk', 'has_sprites': True, 'has_bkg': True,
              'has_window': True, 'has_draw': False, 'has_gb_regs': True,
              'has_sound': True, 'has_banking': True,
              'has_color': False, 'bkg_palettes': 1, 'spr_palettes': 2,
              'has_tile_palettes': False, 'sprite_bpp': 2, 'max_metasprite_tiles': 16}
PLATFORM_CAPS = {
    'gameboy':         dict(_GB_FAMILY),
    'gameboy_color':   dict(_GB_FAMILY, has_color=True, bkg_palettes=8,
                            spr_palettes=8, has_tile_palettes=True),
    # The Pocket's GB core is GBC-capable; the CGB palette path is mirrored
    # into the DMG registers so a DMG-mode core still shows quantized shades.
    'analogue_pocket': dict(_GB_FAMILY, has_color=True, bkg_palettes=8,
                            spr_palettes=8, has_tile_palettes=True),
    'megaduck':        dict(_GB_FAMILY, has_banking=False),
    'sms':             {'framework': 'gbdk', 'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False,
                        'has_sound': True, 'has_banking': False,
                        'has_color': True, 'bkg_palettes': 1, 'spr_palettes': 1,
                        'has_tile_palettes': False, 'sprite_bpp': 2, 'max_metasprite_tiles': 16},
    'gamegear':        {'framework': 'gbdk', 'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False,
                        'has_sound': True, 'has_banking': False,
                        'has_color': True, 'bkg_palettes': 1, 'spr_palettes': 1,
                        'has_tile_palettes': False, 'sprite_bpp': 2, 'max_metasprite_tiles': 16},
    'nes':             {'framework': 'gbdk', 'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False,
                        'has_sound': True, 'has_banking': False,
                        'has_color': True, 'bkg_palettes': 4, 'spr_palettes': 4,
                        'has_tile_palettes': True, 'sprite_bpp': 2, 'max_metasprite_tiles': 16},
    'lynx':            {'framework': 'cc65', 'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': True, 'has_gb_regs': False,
                        'has_sound': True, 'has_banking': False,
                        'has_color': True, 'bkg_palettes': 1, 'spr_palettes': 4,
                        'has_tile_palettes': False, 'sprite_bpp': 4, 'max_metasprite_tiles': 64},
    'pce':             {'framework': 'cc65', 'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False,
                        'has_sound': True, 'has_banking': False,
                        'has_color': True, 'bkg_palettes': 4, 'spr_palettes': 4,
                        'has_tile_palettes': True, 'sprite_bpp': 4, 'max_metasprite_tiles': 64},
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
