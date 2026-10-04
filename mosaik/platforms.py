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
#   emulates it by drawing the 32x32 map as a ring of screen-spanning Suzy
#   row-strip sprites (one per visible row) repositioned each present (the
#   SPRDEMO4 scrolling technique).
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
# - has_banking:  `bank(N)` function placement AND asset-residency DATA banking
#   (the `platform.assets` seam) compile to real banked-ROM code -- the function/
#   data is placed in a switchable ROM bank and a mapper write pages it in.
#   True on the Game Boy family (MBC5 + sdcc __banked far calls), SMS/Game Gear
#   (the Sega mapper, slot 1 via MAP_FRAME1) and the NES (UNROM / iNES mapper 30,
#   _switch_prg0). Off on the Mega Duck (its
#   cart mapper is unverified); on it and every non-GBDK console the annotation is
#   accepted but ignored / the seam stays resident, so one source with banked code
#   still builds everywhere.
# - has_color:    programmable RGB colors (graphics.palette's palette.rgb /
#   set_bkg / set_sprite). The calls exist on *every* console -- on the 4-grey
#   machines (DMG, Mega Duck) colors quantize to shades and apply via
#   BGP/OBP0/OBP1, exactly what real GBC games do on a DMG -- so this flag is
#   documentation + prelude selection, not call gating.
# - bkg_palettes / spr_palettes: usable 4-color palette slots per layer
#   (graphics.palette slot arguments; out-of-range slots are masked or
#   ignored at run time). Slot 0 is the portable guarantee.
# - has_sprite_flip: the sprite hardware can mirror a tile horizontally /
#   vertically (FLIP_X / FLIP_Y). True on the Game Boy family (OAM attr), the
#   NES (OAM attr), and the Lynx / PCE (Suzy / SATB). FALSE on the SMS / Game
#   Gear -- their VDP sprites have no flip bit, so the metasprite engine must
#   NOT reverse the cell layout for a flip there (reversing cells without
#   mirroring the tiles garbles the sprite); use dedicated/pre-mirrored frames
#   for facing on those consoles (see the per-console rulebook).
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
# - bkg_bpp: the native BACKGROUND tile colour depth -- 2 (4 colours, the GB
#   model) on the GB family / NES, 4 (16 colours) on SMS / Game Gear (native
#   VDP 4bpp tiles), the PC Engine (VDC 4bpp characters) and the Lynx (Suzy
#   4bpp strips). The mirror of sprite_bpp for the scene tileset: a >4-colour
#   indexed tileset PNG renders at 16 colours on a bkg_bpp==4 console
#   (`palette.load_bkg16` loads the 16-entry palette) and luma-quantizes to
#   the 2bpp grey ramp on a bkg_bpp==2 console. A <=4-colour tileset / a
#   2bpp console is byte-identical. Opt-in per this cap.
# - max_metasprite_tiles: largest metasprite (graphics.sprite's
#   sprite.set_meta, a W*H block of 8x8 tiles moved as one unit) the console's
#   sprite model comfortably draws. On the Game Boy family it is bounded by
#   the OAM budget (40 objects, 10 per scanline); the Lynx/PCE composite the
#   block into one hardware sprite so they are far more generous. The value
#   documents the ceiling and drives a compile-time warning -- it is not a
#   hard cap (oversized metasprites still emit, they just may drop objects on
#   GB-family scanlines).
# - has_save / save_bytes: battery-backed persistent storage (platform.save).
#   `has_save` gates the stdlib (honest-off like has_window: calling save.* on a
#   console without it is a clear "not supported on target" compile error).
#   `save_bytes` is the capacity in bytes (0 = none). Stage 0 (2026-07-12) ships
#   the GB family (MBC5 cart SRAM at 0xA000, battery -- the 0x1B cart header is
#   already emitted via [build] ram_size, whose value is the effective capacity;
#   8192 = the 8 KB default). Off elsewhere for now: the Lynx (93C46 EEPROM,
#   128 B), SMS/GG (Sega-mapper cart SRAM, ~8 KB) and are staged follow-ups; the
#   NES (GBDK no-op mapper), PCE (console-side BRAM, not HuCard) and Mega Duck
#   (no battery-cart convention) stay off.
_GB_FAMILY = {'framework': 'gbdk', 'screen_cols': 20, 'screen_rows': 18,
              'has_sprites': True, 'has_bkg': True,
              'has_window': True, 'has_draw': False, 'has_gb_regs': True,
              'has_sound': True, 'has_banking': True, 'has_sprite_flip': True,
              'has_color': False, 'bkg_palettes': 1, 'spr_palettes': 2,
              'has_tile_palettes': False, 'sprite_bpp': 2, 'bkg_bpp': 2,
              'max_metasprite_tiles': 16,
              'has_save': True, 'save_bytes': 8192}
PLATFORM_CAPS = {
    'gameboy':         dict(_GB_FAMILY),
    'gameboy_color':   dict(_GB_FAMILY, has_color=True, bkg_palettes=8,
                            spr_palettes=8, has_tile_palettes=True),
    # The Pocket's GB core is GBC-capable; the CGB palette path is mirrored
    # into the DMG registers so a DMG-mode core still shows quantized shades.
    'analogue_pocket': dict(_GB_FAMILY, has_color=True, bkg_palettes=8,
                            spr_palettes=8, has_tile_palettes=True),
    'megaduck':        dict(_GB_FAMILY, has_banking=False, has_save=False, save_bytes=0),
    'sms':             {'framework': 'gbdk', 'screen_cols': 32, 'screen_rows': 24,
                        'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False,
                        'has_sound': True, 'has_banking': True, 'has_sprite_flip': False,
                        'has_color': True, 'bkg_palettes': 1, 'spr_palettes': 1,
                        'has_tile_palettes': False, 'sprite_bpp': 2, 'bkg_bpp': 4,
                        'max_metasprite_tiles': 16,
                        'has_save': False, 'save_bytes': 0},
    'gamegear':        {'framework': 'gbdk', 'screen_cols': 20, 'screen_rows': 18,
                        'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False,
                        'has_sound': True, 'has_banking': True, 'has_sprite_flip': False,
                        'has_color': True, 'bkg_palettes': 1, 'spr_palettes': 1,
                        'has_tile_palettes': False, 'sprite_bpp': 2, 'bkg_bpp': 4,
                        'max_metasprite_tiles': 16,
                        'has_save': False, 'save_bytes': 0},
    'nes':             {'framework': 'gbdk', 'screen_cols': 32, 'screen_rows': 30,
                        'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False,
                        'has_sound': True, 'has_banking': True, 'has_sprite_flip': True,
                        'has_color': True, 'bkg_palettes': 4, 'spr_palettes': 4,
                        'has_tile_palettes': True, 'sprite_bpp': 2, 'bkg_bpp': 2,
                        'max_metasprite_tiles': 16,
                        'has_save': False, 'save_bytes': 0},
    'lynx':            {'framework': 'cc65', 'screen_cols': 20, 'screen_rows': 12,
                        'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': True, 'has_gb_regs': False,
                        'has_sound': True, 'has_banking': False, 'has_sprite_flip': True,
                        'has_color': True, 'bkg_palettes': 1, 'spr_palettes': 4,
                        'has_tile_palettes': False, 'sprite_bpp': 4, 'bkg_bpp': 4,
                        'max_metasprite_tiles': 64,
                        'has_save': False, 'save_bytes': 0},
    'pce':             {'framework': 'cc65', 'screen_cols': 32, 'screen_rows': 28,
                        'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False,
                        'has_sound': True, 'has_banking': False, 'has_sprite_flip': True,
                        'has_color': True, 'bkg_palettes': 4, 'spr_palettes': 4,
                        'has_tile_palettes': True, 'sprite_bpp': 4, 'bkg_bpp': 4,
                        'max_metasprite_tiles': 64,
                        'has_save': False, 'save_bytes': 0},
}


# Consoles whose BACKGROUND (scene tileset) engine renders the native 4bpp
# (16-colour) tier. The bkg mirror of mosaik8_targets.SPRITE_4BPP_ENGINE, kept
# HERE (the shared capability layer) because BOTH the scene transpiler
# (mosaik_scenes, which bakes the `if platform` TILESET fork for exactly these
# consoles) AND the build tool (mosaik8_targets.target_bkg_bpp) read it. Grown
# one stage at a time so a half-finished stage never emits a broken tier:
# Stage 1 = pce, Stage 2 = sms/gamegear, Stage 3 = lynx. EMPTY = the whole tier
# is dormant and every build stays byte-identical (Stage 0).
BKG_4BPP_ENGINE = {'pce', 'sms', 'gamegear'}  # per stage: {'pce'} -> +{'sms','gamegear'} -> +{'lynx'}


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


# Consoles with a HARDWARE 8x16 sprite mode, i.e. where `[build] obj_8x16`
# means something. The GB family runs it off LCDC bit 2; the SMS and the Game
# Gear off VDP R1's sprite-size bit (`SPRITES_8x16`), and the two hardwares
# agree on the semantics that matter here - a tall sprite is a VERTICAL pair
# of consecutive patterns whose low index bit the hardware ignores, so one
# column-major tile stream and one fan serve both.
#
# THE ONE SOURCE OF TRUTH. Four things have to agree about this set or a
# project draws garbage, and three of them are in GENERATED code that outlives
# the decision: the asset conversion (whether tiles reorder column-major), the
# codegen (the fan and the LCDC/VDP write), the generated rooms.mos (whether
# the OAM packing halves each actor's fan) and the generated clips.mos (whether
# a descriptor cell is one object or two stacked ones). rooms.mos and clips.mos
# are target-NEUTRAL, so they carry an `if platform` fork built from
# obj16_guard() rather than a decision baked at generation time.
OBJ16_CONSOLES = frozenset({'gameboy', 'gameboy_color', 'megaduck',
                            'analogue_pocket', 'sms', 'gamegear'})


def obj16_effective(platform, flag) -> bool:
    """`[build] obj_8x16` EFFECTIVE for one target: asked for by the build AND
    on a console that has the mode. Honestly ignored elsewhere (the Lynx and
    the PCE have their own sprite sizing and nothing here drives it) rather
    than half-applied, which would reorder tiles nothing reads that way."""
    return bool(flag) and canonical_platform(platform) in OBJ16_CONSOLES


# Consoles where vm.music's tick can come from the VBL interrupt chain
# (a watchdog tick): the GB family and the z80 pair all expose GBDK's
# add_VBL, and the VBL is the driver's own reference clock (its main-loop
# catch-up targets system.frames(), which IS the VBL count) - so an ISR tick
# keeps the exact average tempo while surviving a blocking room load / box
# repaint. NES is excluded (vm.music degrades to silence there, so an armed
# ISR would tick a stub); Lynx/PCE are cc65 and keep the main-loop catch-up.
MUSIC_ISR_CONSOLES = frozenset({'gameboy', 'gameboy_color', 'megaduck',
                                'analogue_pocket', 'sms', 'gamegear'})


def obj16_guard(indent='') -> str:
    """The mosaik `if platform == ...` line the target-neutral generated
    modules fork on, built from OBJ16_CONSOLES so the guard cannot name a
    different set from the codegen that has to read the data."""
    return indent + 'if ' + ' or '.join(
        'platform == "%s"' % p for p in sorted(OBJ16_CONSOLES)) + ' {'

# ---------------------------------------------------------------------------
# DEGRADED LOWERINGS (review E-7): verbs and constants that compile, link and
# run on a console and quietly do LESS than they say. They are not errors - an
# UNSUPPORTED verb is already a compile-time error, by design, and that is
# exactly why these are silent: they are the ones the stdlib map still
# answers. So the build has to say them out loud instead.
#
# Every entry names the SAME condition the emitter uses, so a note cannot
# claim a degradation the backend does not have: `has_sprite_flip` is what
# `gbs_move_sprite` reads for its FLIP arms, `has_tile_palettes` what
# `gbs_bkg_attrs` reads before it becomes a `(void)` cast. The one fact no cap
# carries - which INPUT_* constants a console emits as literal 0 - is passed
# IN by the generator that just emitted them, rather than restated here.
# `tests/degraded_verbs_test.py` checks every note against the generated C of
# every console.
#
# A `('call', alias, field)` entry fires when the program calls `alias.field`;
# a `('const', NAME)` entry when it mentions NAME anywhere.
# ---------------------------------------------------------------------------

def degraded_uses(platform, dead_consts=(), real_attrs=False):
    """[(trigger, message)] for `platform`; the caller filters by what is used.

    `dead_consts` is the names the target defines as literal 0 (the Lynx has
    no Start or Select button, so `INPUT_START` is 0 there and every test
    against it is false)."""
    platform = canonical_platform(platform)
    caps = PLATFORM_CAPS[platform]
    out = [
        (('call', 'input', 'pressed'),
         "input.pressed is input.held on every console (the runtime has no "
         "edge detector), so a HELD button reads as pressed on every frame - "
         "latch the previous state yourself if you want an edge"),
    ]
    if not caps['has_sprite_flip']:
        for name in ('FLIP_X', 'FLIP_Y'):
            out.append((('const', name),
                        "%s is ignored by this console's sprite hardware; the "
                        "art has to be pre-mirrored (which is what the "
                        "animation pipeline's soft-flip bake does)" % name))
    # `real_attrs`: the PC Engine's attribute upload is REAL when its
    # background engine and the palette prelude are both in the program (it
    # rides gbs_bkg_palette_fill); the generator passes that fact in.
    if (not caps['has_tile_palettes'] or caps['framework'] == 'cc65') \
            and not real_attrs:
        out.append((('call', 'bkg', 'set_attrs'),
                    "bkg.set_attrs is an honest no-op here - this console has "
                    "no per-tile background palette map; its colour comes "
                    "from the 4bpp tier or from a single palette"))
    for name in dead_consts:
        out.append((('const', name),
                    "%s is 0 on this console (it has no such button), so "
                    "every test against it is false" % name))
    return out

