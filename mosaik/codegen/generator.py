"""Shared code generator (backend-agnostic). Backend specifics are
mixed in from gbdk.GbdkBackend and cc65.Cc65Backend."""

import dataclasses
import re

from ..ast_nodes import *  # noqa: F401,F403
from ..platforms import (PLATFORM_CAPS, canonical_platform, degraded_uses,
                         framework_for_platform, platform_caps)
from .gbdk import GbdkBackend
from .cc65 import Cc65Backend
from .gen_modules import EmitModulesMixin
from .gen_streaming import StreamingMixin
from .gen_decls import DeclEmitMixin
from .gen_fnptr import FnPtrDeclMixin
from .gen_expr import ExprStmtMixin
from .gen_budgets import BudgetsMixin


class CodeGenerator(GbdkBackend, Cc65Backend, EmitModulesMixin, StreamingMixin, DeclEmitMixin, FnPtrDeclMixin, ExprStmtMixin, BudgetsMixin):
    """Code generator that emits GBDK C *or* cc65 C for the target console.

    The backend (prelude + stdlib lowering) is selected in generate() from
    the console's framework; backend-specific emitters and call maps come
    from the GbdkBackend / Cc65Backend mixins.
    """

    # mosaik primitive types -> C types.
    PRIMITIVE_C_TYPES = {
        'u8': 'uint8_t',
        'i8': 'int8_t',
        'u16': 'uint16_t',
        'i16': 'int16_t',
        'bool': 'uint8_t',
        'addr': 'uint16_t',
        'void': 'void',
    }

    # Every (module, func) pair known to any backend. Used to tell
    # "unsupported on this target" (a clear compile error) apart from an
    # ordinary cross-module call that lowers to `module_func(...)`.
    ALL_STDLIB_CALLS = (set(GbdkBackend.STDLIB_CALLS_GBDK)
                        | set(Cc65Backend.STDLIB_CALLS_CC65_CORE)
                        | set(Cc65Backend.STDLIB_CALLS_CC65_DRAW)
                        | set(Cc65Backend.STDLIB_CALLS_CC65_SPRITE)
                        | set(Cc65Backend.STDLIB_CALLS_CC65_BKG)
                        | set(Cc65Backend.STDLIB_CALLS_CC65_PALETTE))

    # Stdlib calls gated by a PLATFORM_CAPS capability. On any backend, calling
    # one of these on a console whose registry entry lacks the capability is
    # the clear "not supported on target" compile error from _gen_call, not a
    # link failure on an undefined symbol.
    CALLS_NEEDING_WINDOW = {('window', 'set_tiles'), ('window', 'move'),
                            ('video', 'show_window'), ('video', 'hide_window')}
    CALLS_NEEDING_BKG = {('bkg', 'set_data'), ('bkg', 'set_data_pal'),
                         ('bkg', 'set_data_native'),
                         ('bkg', 'set_tiles'),
                         ('bkg', 'scroll'), ('bkg', 'move')}
    CALLS_NEEDING_SPRITES = {('sprite', 'set_data'), ('sprite', 'set_tile'),
                             ('sprite', 'get_tile'), ('sprite', 'set_prop'),
                             ('sprite', 'set_meta'),
                             ('sprite', 'move'), ('sprite', 'set_palette'),
                             ('sprite', 'set_meta_palettes'),
                             ('video', 'show_sprites'),
                             ('video', 'hide_sprites')}
    CALLS_NEEDING_SOUND = {('sound', 'beep'), ('sound', 'stop'),
                           ('sound', 'beep2'), ('sound', 'stop2')}
    # Per-tile background palette selection (GBC attribute map, PCE BAT bits,
    # NES attribute table). The plain palette.* calls are NOT gated -- they
    # exist on every console and quantize to greys on the 4-grey machines.
    CALLS_NEEDING_TILE_PALETTES = {('bkg', 'set_palette')}
    # platform.save (battery-backed cart RAM). Gated on has_save so a save.* call
    # on a no-battery console is the clear unsupported diagnostic, not a link fail.
    CALLS_NEEDING_SAVE = {('save', 'enable'), ('save', 'disable'),
                          ('save', 'write_u8'), ('save', 'read_u8')}

    # Game Boy hardware-register constants. Emitted as prelude #defines only on
    # has_gb_regs consoles; referencing one anywhere else is a clear compile
    # error (the addresses are meaningless on other machines).
    GB_REG_CONSTANTS = {'REG_DIV', 'REG_NR10', 'REG_BGP', 'REG_OBP0', 'REG_OBP1'}

    BINARY_C_OPERATORS = {
        '+': '+', '-': '-', '*': '*', '/': '/', '%': '%',
        '==': '==', '!=': '!=', '<': '<', '>': '>', '<=': '<=', '>=': '>=',
        'and': '&&', 'or': '||',
        '&': '&', '|': '|', '^': '^', '<<': '<<', '>>': '>>',
    }

    def __init__(self):
        self.output = []
        self.platform = 'gameboy'
        self.framework = 'gbdk'
        self._stmt_ctx = []  # break-context stack (loop vs switch)
        self.caps = PLATFORM_CAPS['gameboy']
        self.cc65_profile = None
        self.cc65_bkg_imported = False
        self.palette_imported = False
        # The build-supplied compile-time defines (see compiler.py), for the
        # few prelude helpers whose SHAPE follows one: absent = every flag off.
        self.defines = {}
        self.stdlib_calls = self.STDLIB_CALLS_GBDK
        self.assets = []         # [(name, data, bpp)] from the asset pipeline
        self.asset_palettes = []  # [(name, [(r,g,b)] x 4)] of indexed-PNG assets
        self.asset_palettes16 = []  # [(name, [(r,g,b)] x 16)] of 4bpp assets
        self.asset_sprites = []  # [(sprite, tile_offset, w_tiles, h_tiles)] from sheets
        self.sprite_src_bpp = 2  # 2 (GB 2bpp) or 4 (Lynx/PCE 16-colour)
        # Background tile-table budget for the cc65 Lynx bkg engine. The engine
        # allocates a resident gbs_bkg_tileset[N][16] in the scarce ~46.6 KB MAIN
        # (256 -> 4 KB of BSS). 256 is the byte-identical default (any u8 map tile
        # index fits); a project that knows it uses fewer tiles can lower it via
        # `[build] bkg_max_tiles` to reclaim (256-N)*16 bytes. The author's
        # contract: it must be >= the world's real tile count (incl. any
        # top-of-table animated/REPLACE_TILE hidden indices), exactly like
        # `rom_size` is the author's cartridge-geometry contract.
        self.bkg_max_tiles = 256
        # Requested (explicit `[build]`) budgets; None = auto-derive in generate()
        # for a VM8 game (see _resolve_lynx_bkg_budgets). Kept distinct from the
        # RESOLVED values above/below so "unset" can trigger the derivation while an
        # explicit knob is honoured verbatim.
        self._bkg_max_tiles_req = None
        self._bkg_strip_w_req = None
        # cc65 Lynx ROW-strip bkg engine strip width (in tiles). None = the full
        # scroll-period default (byte-identical); a smaller value shrinks the
        # ~13.5 KB gbs_bkg_strip[][] BSS when the world's scenes provably cannot
        # scroll that far (a VM8 game, whose camera clamps to scene bounds).
        self.bkg_strip_w = None
        # cc65 Lynx sprite tile-table budget: the Suzy engine allocates a resident
        # gbs_tiles[N][GBS_TILE_BYTES] in MAIN (40 -> ~1.3 KB of BSS, converted
        # from the 2bpp source at runtime). None = the full 40 default
        # (byte-identical); a smaller value (auto-derived from the tiles a program
        # UPLOADS via sprite.set_data, or set explicitly) reclaims (40-N)*33 bytes.
        self.sprite_max_tiles = None
        self._sprite_max_tiles_req = None
        # cc65 Lynx sprite SLOT budget: gbs_scb[N] + the tile/meta side tables
        # (~27 B of MAIN per slot at N=40). None = the byte-identical 40; an
        # explicit `[build] sprite_max_slots` lowers it for a game whose highest
        # slot is known. Never auto-derived (slot ids are runtime values).
        self.sprite_max_slots = None
        self.cc65_spr_need = None   # (tiles, slots) from rooms SPR_*_NEED
        self.struct_types = {}   # name -> StructType
        self.enum_types = set()  # names of enum types
        # Asset streaming (Lynx cart): streamed-asset
        # const arrays are moved OUT of the resident image into a cart archive.
        self._streaming = False  # Lynx + assets.use/ptr calls present
        self._stream_mode = None # None / 'lynx' (cart archive) / 'gb' (ROM banks)
        self.streamed = {}       # mangled C symbol -> stream id (Lynx) / bank (GB)
        self.stream_offsets = [] # archive-relative byte offset per id (Lynx)
        self.stream_lengths = [] # byte length per id (Lynx)
        self.streamed_archive = b""  # the cart-archive blob (read by the build)
        # Range-windowed streaming (paint_table + [world] stream):
        # a concatenated MAPS/COLLISION array is archived whole, and the seam
        # streams the CURRENT room's WINDOW (assets.use_range/ptr_range/range_byte)
        # into a slot sized to the widest scene -- so paint()/map_tile stay O(1)
        # code AND the map data streams off-resident. A range base is a streamed
        # symbol windowed this way (not whole-loaded).
        self._range_bases = set()  # streamed C symbols windowed by the range seam
        self._range_maxwin = 0     # widest window (bytes) -> range cache slot size
        # Symbols loaded WHOLE (assets.use/ptr). A program may use both seams at
        # once -- a per-scene tileset loads whole while the concatenated maps
        # window -- so the two caches are emitted independently, and the whole
        # cache's slot size is measured over THESE symbols only (a range base's
        # concatenated array is archived whole but never whole-LOADED, so sizing
        # a RAM slot to it would be enormous and pointless).
        self._whole_syms = set()
        # Whole-loaded symbols that LOAD THROUGH into the background tile table
        # (a per-scene tileset: copied out by set_bkg_data, never read again),
        # so they are streamed straight into it and cost no cache slot. Empty =
        # byte-identical. See gen_streaming._collect_bkg_loadthrough.
        self._bkg_loadthrough = set()
        # Lynx sprite SHEETS streamed from the cart under `[world] stream`:
        # `<name>_tiles` symbol -> its archive block (1 KB units). Uploaded
        # tile by tile by gbs_spr_data_stream. Empty = byte-identical. See
        # gen_streaming._pack_lynx_sheets.
        self.sheet_stream = {}
        # Bytecode-blob PAGE streaming (VM8, §7.1 #3): a large const blob read
        # byte-by-byte through `assets.code_byte(blob, off)` (the interpreter's
        # fetch seam) is archived on the Lynx and read a page at a time into a small
        # current-page cache, freeing the whole RODATA blob from the tight MAIN.
        self.streamed_code_sym = None  # mangled C symbol of the streamed code blob
        self.streamed_code_base = 0    # its byte offset within streamed_archive
        self.streamed_code_len = 0     # its byte length
        # A SECOND streamable code_byte blob (the VM8 STRINGS text blob, workstream
        # B phase 2): additive alongside the primary CODE blob above, with its own
        # page buffer/reader so cold text reads never thrash the hot instruction page.
        self.streamed_code2_sym = None
        self.streamed_code2_base = 0
        self.streamed_code2_len = 0
        # A THIRD streamable code_byte blob (the songs.mos CELLS data, audio plan
        # Tier D): so a game with big SCRIPTS + big DIALOGUE TEXT can still stream
        # a big imported SONG (each gets its own page buffer). A 4th big blob stays
        # resident (may overflow MAIN with a clear ld65 error -- extend the slots
        # then, same clone).
        self.streamed_code3_sym = None
        self.streamed_code3_base = 0
        self.streamed_code3_len = 0
        # GB-family ROM banking of streamed const arrays (see _collect_streamed):
        # the cart IS CPU-addressable, so a streamed const stays in ROM, placed
        # in an MBC5 bank, and the seam bank-switches to read it.
        self.data_bank_syms = {}  # bank number -> [streamed C symbol, ...]
        self.data_bank_decls = {} # streamed C symbol -> its VarDecl
        # PRELUDE data that banks the same way (bank0 plan O5). A prelude table
        # is raw C, not a mosaik const, so it cannot ride data_bank_decls -- it
        # registers its finished definition text here instead.
        # sym -> (bank, c_definition_text); see _prelude_data_bank.
        # The mosaik statement codegen is currently on, so a RuntimeError
        # from an emitter can be reported with a source line instead of a
        # Python traceback (review L-9; set in gen_statement, read by
        # compile_program's handler).
        self._cur_line = None
        self._cur_file = None
        self._called_verbs = None
        self._called_verbs_for = None
        self.prelude_bank_defs = {}
        self._func_banks = set()  # bank numbers used by bank(N) functions
        # Build-driven cold-code banking (`[build] code_banks`, GB family):
        # whole modules' function bodies are placed in a switchable ROM bank,
        # with address-taken (seam-registered) functions split into a resident
        # stub + a banked body. Empty = feature off (byte-identical).
        self.code_banks = []        # module names requested for code banking
        # `[assets] font`: a custom 96-glyph 1bpp table replacing the built-in
        # font in the GLYPH-BUFFER text mode's emitted prelude (None = built-in).
        self.glyph_font_override = None
        self.glyph_widths_override = None
        # `[build] obj_8x16`: 8x16 OBJ sprite mode (G4). Requested by the
        # build; honoured only on the GB family (it is an LCDC bit) -- see
        # _obj16() for the effective test.
        self.obj_8x16 = False
        # `[build] sms_start_button` -- also answer J_START from the SMS pad's
        # button 1. The pad LABELS it "1 START" and that is how an SMS title
        # screen is started, but the two are one bit as far as a program is
        # concerned: a script attached to BOTH `a` and `start` then fires twice
        # on one press (the phantom-button hazard J_SELECT documents), which on
        # the converted reference-engine sample opens its title menu twice. So it is a
        # per-project CHOICE, off by default, and the console's PAUSE button
        # stays a real Start either way.
        self.sms_start_button = False
        self._code_bank_num = 0     # first code bank (0 = feature off)
        self._code_bank_of = {}     # module name -> its own code bank number
        self._bank_candidates = []  # [(module, FunctionDecl)] considered in Phase B
        # Bank-neutrality wrappers: with code banking active, any function whose
        # body switches the ROM window (a GB streamed-seam read) must restore the
        # entry bank before returning, or a BANKED caller resumes under the wrong
        # bank. id(FunctionDecl) members get the save/restore wrapper.
        self._bank_neutral_funcs = set()
        self._bank_neutral_active = False   # emitting inside a wrapped function
        self._current_ret_ctype = 'uint8_t' # wrapped function's C return type
        # Same-bank de-trampolined locals: id(FunctionDecl) members are
        # emitted without BANKED. See _collect_near_bank_locals.
        self._near_bank_funcs = set()
        # Const arrays co-located in a code bank (read only by banked code):
        # mangled C name -> bank number. See _collect_bank_local_consts.
        self._bank_local_consts = {}
        # Sprite sheets placed in a DATA bank and uploaded through the far
        # read (`gbs_spr_data_far`): C symbol -> bank. A code bank is one
        # 16 KB window and the art was competing with the module's code for
        # it. See _collect_bank_local_consts.
        self.asset_far_bank = {}
        # ROM banking (bank(N) function placement, GB family only).
        # SDCC's `#pragma bank` is file-scoped, so each
        # used bank becomes its own C translation unit in bank_units; the
        # build tool writes and links them alongside the main TU.
        self.banking_active = False
        self.bank_units = {}     # bank number -> generated C source
        # cc65 banking (cc65_bank): assembly units the build links beside the
        # C (name -> ca65 source), and the highest logical bank used (the
        # build sizes the HuCard and its ld65 config from it). Empty / 0 for
        # every program that does not bank.
        self.asm_units = {}
        self.cc65_max_bank = 0
        self.cc65_used_banks = []
        # Resident thunks of the banked cc65 functions: [(c_name, bank)],
        # collected as their prototypes are emitted (cc65_bank).
        self._cc65_thunks = []
        # Cross-module linking state (see _collect_modules).
        self.multi_module = False
        self.module_symbols = {}   # module name -> symbol-table dict
        self.module_aliases = {}   # alias (last name segment) -> module name
        self.current_module = None
        self.current_symbols = set()  # module-level names of current module
        self.current_imports = {}     # alias -> imported program-module name
        self.local_names = set()      # params/locals of the current function

    # -- top level ---------------------------------------------------------

    def _pce_attrs_real(self):
        """Whether `bkg.set_attrs` has a REAL body on this build: the PC Engine
        with its background engine AND the palette prelude, where each cell's
        slot rides `gbs_bkg_palette_fill` into the BAT entry's palette bits.
        One answer for the emitter and the degraded-verb note."""
        return (self.platform == 'pce' and self.palette_imported
                and self.caps.get('has_bkg') and self.cc65_bkg_imported)

    def _report_degraded(self, program):
        """Say, once per build, which verbs this target lowers to LESS than
        they say (review E-7).

        An unsupported verb is already a compile-time error, so what is left
        is the honest-but-degraded set: a `(void)` no-op, a constant that is
        0, an edge that is really a level. Those compile, link and run, and
        the only way an author finds out today is by watching the ROM do the
        wrong thing. The table lives in `mosaik.platforms` beside the caps it
        keys off; the one fact no cap carries - which INPUT_* constants this
        target defines as literal 0 - is read back off the port table here
        rather than restated there."""
        dead = [n for n in ('INPUT_START', 'INPUT_SELECT')
                if str(self._input_const(n)).strip() == '0']
        real_attrs = self._pce_attrs_real()
        for trigger, note in degraded_uses(self.platform, dead, real_attrs):
            if trigger[0] == 'call':
                used = self._program_uses_call(program, trigger[1], trigger[2])
            else:
                used = self._program_uses_identifier(program, trigger[1])
            if used:
                print("    Note: %s" % note)
        # The one degradation that is a per-project CHOICE rather than a
        # property of the console, so it cannot live in the platform table:
        # `[build] sms_start_button` folds button 1 into J_START, and the two
        # are then ONE bit - a script bound to both fires twice.
        if (self.sms_start_button
                and self._program_uses_identifier(program, 'INPUT_A')
                and self._program_uses_identifier(program, 'INPUT_START')):
            print("    Note: [build] sms_start_button makes pad button 1 answer "
                  "INPUT_A and INPUT_START from the same bit, so anything bound "
                  "to both fires twice on this target")

    def _input_const(self, name):
        """What this target's prelude defines `name` as (cc65 reads it from
        the console profile; every GBDK port maps to a real J_* bit)."""
        if self.caps['framework'] != 'cc65':
            return 'J_' + name.split('_', 1)[1]
        prof = (self.CC65_PROFILES.get(self.platform)
                or self.CC65_PROFILES['lynx'])
        return prof.get(name.lower(), '0')   # 'INPUT_START' -> 'input_start'

    def generate(self, program) -> str:
        # ONE traversal for the ~40 `_used` flags below (review L-10). They are
        # a pure function of the AST and each used to walk the whole program by
        # itself: measured on the reference-engine sample conversion, 93 walks costing 18.1 s, 58% of
        # the compile. Built FIRST, so every flag reads the set, and dropped at
        # the end - the AST is rewritten between generate() calls.
        self._called_verbs = self._collect_called_verbs(program)
        self._called_verbs_for = program
        self.output = []
        self.struct_types = {}
        self.enum_types = set()
        # Reset the asset-residency state (the generator may be reused across
        # builds): Lynx cart streaming vs GB-family ROM banking are decided per
        # call in _collect_streamed.
        self._streaming = False
        self._stream_mode = None
        self.streamed = {}
        self.stream_offsets = []
        self.stream_lengths = []
        self.streamed_archive = b""
        self._range_bases = set()
        self._range_maxwin = 0
        self._whole_syms = set()
        self._bkg_loadthrough = set()
        self.sheet_stream = {}
        self.streamed_code_sym = None
        self.streamed_code_base = 0
        self.streamed_code_len = 0
        self.streamed_code2_sym = None
        self.streamed_code2_base = 0
        self.streamed_code2_len = 0
        self.streamed_code3_sym = None
        self.streamed_code3_base = 0
        self.streamed_code3_len = 0
        self.data_bank_syms = {}
        self.data_bank_decls = {}
        self.prelude_bank_defs = {}
        self._func_banks = set()
        self._code_bank_num = 0
        self._code_bank_of = {}
        self._bank_candidates = []
        self._bank_neutral_funcs = set()
        self._bank_neutral_active = False
        self._bank_local_consts = {}
        self.asset_far_bank = {}
        self._near_bank_funcs = set()
        self.asm_units = {}
        self.cc65_max_bank = 0
        self.cc65_used_banks = []
        self._cc65_thunks = []

        # Pick the backend (prelude + stdlib lowering) for the target console.
        # The PLATFORM_CAPS registry decides which stdlib calls exist here.
        self.framework = framework_for_platform(self.platform)
        self.caps = platform_caps(self.platform)
        # The palette prelude blocks (gbs_rgb, the palette setters, the Lynx
        # pen partition, ...) are emitted only for programs that import
        # graphics.palette, so every existing program keeps byte-identical
        # output (same pattern as the Lynx bkg engine below).
        # sprite.set_palette / bkg.set_palette are spec'd members of
        # graphics.sprite/bkg but their helpers live in the palette prelude,
        # so a USE of either must also emit it -- importing only
        # graphics.sprite and calling set_palette used to compile to an
        # undefined gbs_ helper and fail at the C level.
        self.palette_imported = (any(
            imp.module_name == 'graphics.palette'
            for module in program.modules for imp in module.imports)
            or self._program_uses_call(program, 'sprite', 'set_palette')
            or self._program_uses_call(program, 'bkg', 'set_palette'))
        # palette.load_bkg_set / load_sprite_set: load a RUN of palette slots
        # out of one table (the per-scene palette set of a coloured world).
        # Gated like every other palette extra, so non-users stay
        # byte-identical; the bodies live in the palette prelude beside the
        # single-slot setters they call.
        self.palette_set_used = (
            self._program_uses_call(program, 'palette', 'load_bkg_set')
            or self._program_uses_call(program, 'palette', 'load_sprite_set'))
        if self.palette_set_used:
            self.palette_imported = True
        # palette.fade(level): darken every LOADED palette towards black. The
        # reference-engine CGB fade -- BGP/OBP0/OBP1 (what vm.fx ramps on a DMG) are
        # IGNORED in CGB mode, so a coloured screen can only be faded by
        # scaling the palettes themselves. That needs a RAM shadow of what the
        # program asked for plus a scale on every palette write, so it is gated
        # on its own use: a program that never fades keeps the plain write path
        # and is byte-identical.
        # system.frames(): a free-running DISPLAY-frame counter. Its whole point
        # is to be independent of how long the game loop takes, so anything
        # rate-sensitive (the vm.music tick) can catch up instead of running at
        # whatever the loop's frame rate happens to be. Gated on its own use, so
        # a program that never asks is byte-identical.
        self.frames_used = self._program_uses_call(program, 'system', 'frames')
        self.input_raw_used = self._program_uses_call(program, 'input', 'raw')
        self.bank_enter_used = self._program_uses_call(program, 'assets', 'bank_enter')
        # system.music_isr / system.music_hold: vm.music's VBL-interrupt tick
        # Both call sites live behind the
        # always-supplied VM_MUSIC_ISR define, so on a non-user (or a console
        # that keeps the main-loop catch-up) they are folded out before this
        # scan runs and nothing is emitted - byte-identical off.
        self.music_isr_used = self._program_uses_call(program, 'system',
                                                      'music_isr')
        self.music_hold_used = self._program_uses_call(program, 'system',
                                                       'music_hold')
        self.music_tick_used = self._program_uses_call(program, 'system',
                                                       'music_tick')
        self.palette_fade_used = self._program_uses_call(program, 'palette',
                                                         'fade')
        if self.palette_fade_used:
            self.palette_imported = True
        # bkg.set_attrs: the per-cell background palette-slot upload (the
        # attribute mirror of bkg.set_tiles). Its helper needs no palette
        # machinery, so it is gated on its own use alone.
        self.bkg_attrs_used = self._program_uses_call(program, 'bkg',
                                                      'set_attrs')
        # bkg.edge_mask(on): the SMS left-column blank a column-streamed
        # level needs (see _emit_gbdk_edge_mask). By USE, so a program that
        # never streams is byte-identical.
        self.edge_mask_used = self._program_uses_call(program, 'bkg',
                                                      'edge_mask')
        # sprite.vbl_hold(on): hold the shadow-OAM -> SAT copy across a frame's
        # sprite writes so they commit as a unit (see _emit_gbdk_vbl_hold). By
        # USE, so a program that never calls it is byte-identical.
        self.vbl_hold_used = self._program_uses_call(program, 'sprite',
                                                     'vbl_hold')
        # sprite.cut_y(y): park sprite objects at/below a screen row, so a box
        # plotted into the background is not covered by them. By USE.
        self.spr_cut_used = self._program_uses_call(program, 'sprite', 'cut_y')
        # sprite.font_glyph(tile, ch): one console-font glyph as a sprite tile,
        # read from the font the toolchain links. By USE (byte-identical off).
        self.spr_font_glyph_used = self._program_uses_call(program, 'sprite',
                                                           'font_glyph')
        # bkg.set_data_pal / sprite.set_data_pal: upload 2bpp tiles rendered
        # through a PALETTE SLOT. Real on SMS/GG, whose VDP stores every tile
        # at 4bpp: the 2bpp expansion can target CRAM entries slot*4.. instead
        # of the fixed 0..3 (set_tile_2bpp_data's nibble map), which is what
        # gives those consoles per-TILE background colour and per-KIND sprite
        # colour off the same 2bpp art every other console uses. A plain
        # slot-ignoring upload elsewhere. Gated on use (byte-identical off);
        # scanned post-conditional-compilation, so the `if platform` fork the
        # scene transpiler emits does not cost the other consoles the helper.
        self.bkg_data_pal_used = self._program_uses_call(program, 'bkg',
                                                         'set_data_pal')
        # sprite.set_meta_palettes: a palette PER CELL of one metasprite. Needs
        # a small per-base pointer table and a second fan, so it is gated on
        # its own use -- a game that colours a whole actor at once keeps the
        # cheaper uniform path and stays byte-identical.
        self.meta_palettes_used = self._program_uses_call(
            program, 'sprite', 'set_meta_palettes')
        # `graphics.palette` grew from "a handful of setters" into a tier with
        # per-tile fills, 16-colour loads and per-scene SETS, and every byte of
        # it is RESIDENT (a prelude helper cannot bank). So the entry points a
        # program does not call are no longer emitted: importing the module
        # gets you gbs_rgb + the two slot setters everything else is built on,
        # and each verb beyond that pays only when used. Measured on the
        # reference-engine import, where the colour tier's own prelude was what took
        # the CGB build past the resident image.
        self.bkg_palette_fill_used = self._program_uses_call(
            program, 'bkg', 'set_palette')
        self.sprite_palette_used = self._program_uses_call(
            program, 'sprite', 'set_palette')
        self.palette_load_used = (
            self._program_uses_call(program, 'palette', 'load_bkg')
            or self._program_uses_call(program, 'palette', 'load_sprite'))
        self.palette_rgb_used = self._program_uses_call(program, 'palette',
                                                        'rgb')
        # palette.load_native (SMS/GG native-format CRAM load): the generated
        # BANKED scenes.load_palettes converts colours itself, so a program
        # whose ONLY palette use is load_native can drop the whole resident
        # gbs_rgb (with its three divisions) -- see _sms_rgb_needed.
        self.pal_native_used = self._program_uses_call(program, 'palette',
                                                       'load_native')
        # gbs_set_bkg_palette / gbs_set_spr_palette are the two the rest is
        # built on, so they stay whenever anything still calls them -- which on
        # the Game Boy Color is nothing when only the per-scene SET loaders are
        # used (they drive the hardware directly there, see
        # _emit_palette_set_real).
        self.palette_setter_used = (
            self._program_uses_call(program, 'palette', 'set_bkg')
            or self._program_uses_call(program, 'palette', 'set_sprite')
            or self.palette_load_used
            # ... or on the consoles whose SET loaders route through them.
            # The GBC drives set_bkg_palette directly, and SMS/GG write CRAM
            # entries directly too (gbs_load_set) - forcing the pair there
            # was ~150 B of dead resident prelude on a console counted in
            # tens of spare bytes.
            or (self.palette_set_used
                and self.platform not in ('gameboy_color', 'sms',
                                          'gamegear')))
        # Metasprites (graphics.sprite's sprite.set_meta) add a small per-slot
        # meta table + a flip-aware fan-out to gbs_move_sprite. Emitted only
        # when the program actually calls set_meta, so non-metasprite programs
        # keep byte-identical output (golden snapshots unchanged).
        # sprite.set_meta_mask is set_meta plus a per-COLUMN blank mask (a
        # SPARSE frame: the reference engine places a metasprite's tiles at authored
        # offsets and leaves gaps, so a composed rectangle has empty cells
        # that must cost neither an OAM object -- the GB draws 10 per
        # scanline, and a blank column stole the eleventh letter of a
        # title banner -- nor a tile in VRAM). It rides the same meta
        # tables, so it implies the metasprite layer.
        self.meta_mask_used = self._program_uses_call(program, 'sprite',
                                                      'set_meta_mask')
        # sprite.set_meta_list is the per-OBJECT descriptor form (the reference engine's
        # metasprite_t): n entries of (dy, dx, dtile, props) at authored
        # offsets, tiles repeating freely. Emitted only when called -- the
        # tables + walker are real bytes and the tightest target (the GB
        # Studio conversion's GBC) has double-digit bank-0 spare.
        self.meta_list_used = self._program_uses_call(program, 'sprite',
                                                      'set_meta_list')
        # sprite.move_world is sprite.move in SIGNED screen coordinates, so
        # the fan can PARK a column that has scrolled off an edge rather than
        # let its u8 position wrap onto the opposite one. Emitted only when
        # called, and the clip bounds are a ONE-SHOT the world move sets and
        # clears - so plain `sprite.move` keeps its own behaviour and a
        # program that never asks is byte-identical.
        self.meta_clip_used = self._program_uses_call(program, 'sprite',
                                                      'move_world')
        self.metasprite_used = (self.meta_mask_used or self.meta_list_used
                                or self.meta_clip_used
                                or self._program_uses_call(
                                    program, 'sprite', 'set_meta'))
        # Sprite asset depth. Assets arrive as (name, data, bpp); older callers
        # (and the test suite) pass plain (name, data) -- those are 2bpp. The
        # build is "4bpp mode" when any sprite asset was encoded 4bpp (only
        # happens on a sprite_bpp==4 console with a >4-colour PNG, decided by
        # mosaik_assets.build_is_4bpp), which widens the Lynx sprite engine to
        # 16-colour literals. Default 2 keeps every existing program identical.
        self.assets = [(a if len(a) == 3 else (a[0], a[1], 2))
                       for a in self.assets]
        self.sprite_src_bpp = 4 if any(b == 4 for _n, _d, b in self.assets) else 2
        # palette.load_sprite16 (16-colour Mikey-pen load for 4bpp sprites) --
        # gated like the metasprite layer so non-users stay byte-identical.
        self.load_sprite16_used = self._program_uses_call(program, 'palette',
                                                          'load_sprite16')
        # palette.load_bkg16 (16-colour BACKGROUND palette load for a 4bpp
        # tileset) -- the bkg mirror, gated the same way so non-users stay
        # byte-identical. Takes a 5-5-5 RGB word array (scenes.BKG_PALETTE16)
        # and the backend body rounds each entry to the target's native depth
        # at runtime (a no-op stub on a 2bpp console / where no 4bpp bkg engine).
        self.load_bkg16_used = self._program_uses_call(program, 'palette',
                                                       'load_bkg16')
        # native.lynx escape hatch (fade/screen-shake): emitted only when the
        # program imports it, so non-users stay byte-identical. Real on the
        # Lynx, a no-op on every other console (generalized fallback).
        self.native_lynx_imported = any(
            imp.module_name == 'native.lynx'
            for module in program.modules for imp in module.imports)
        # native.huge (hUGEDriver): emitted only when the program imports it, so
        # non-users stay byte-identical AND the driver object is only added to
        # the link for a program that actually plays through it. Unlike
        # native.lynx this has NO no-op fallback off the GB family -- see
        # stdlib.py; the build refuses the import there instead.
        self.native_huge_imported = any(
            imp.module_name == 'native.huge'
            for module in program.modules for imp in module.imports)
        # W7h: the `6xy` call-routine thunk, its queue and the driver's routines
        # table. Emitted only when the BLOB attaches a routine (the build states
        # VM_OP_MUSIC_ROUTINE off the bytecode) - so every other hUGE project's
        # prelude is byte-identical, and its song descriptors keep the NULL
        # `routines` field that made the pre-W7h engine safe.
        self.huge_routines_used = bool(
            self.native_huge_imported
            and self.defines.get('VM_OP_MUSIC_ROUTINE'))
        if self.native_huge_imported and not self.caps.get('has_gb_regs'):
            # `has_gb_regs` IS the GB family (Game Boy, Color, Pocket, Mega Duck).
            # The SMS/GG/NES share the GBDK backend but not the APU, and CrossZGB's
            # "sms/hUGEDriver.c" is a 20-line NO-OP STUB, not a port -- so there is
            # nothing to fall back to. Say so at compile time rather than linking a
            # driver that cannot make a sound.
            raise RuntimeError(
                "native.huge (hUGEDriver) is not available on target '%s': it is "
                "Game Boy APU assembly and runs only on the GB family (gameboy, "
                "gameboy_color, analogue_pocket, megaduck). Use the portable "
                "vm.music driver on this console." % self.platform)
        # platform.save (battery SRAM): emit the gbs_save_* helpers only when the
        # program imports it, so every existing program stays byte-identical.
        self.save_imported = any(
            imp.module_name == 'platform.save'
            for module in program.modules for imp in module.imports)
        # Portable one-shot SFX set (sound.sfx(id)); a thin wrapper over the
        # single beep channel, emitted only when used (golden stays identical).
        self.sound_sfx_used = self._program_uses_call(program, 'sound', 'sfx')
        # A SECOND simultaneous tone voice (sound.beep2/stop2) -- e.g. MUSIC on its
        # own channel while SFX use the primary beep. Emitted only when used; when
        # used, the primary stop switches to per-channel silence (so an ending SFX
        # doesn't power off the whole chip + kill the music). Byte-identical unused.
        self.sound_beep2_used = (self._program_uses_call(program, 'sound', 'beep2')
                                 or self._program_uses_call(program, 'sound', 'stop2'))
        # graphics.text helpers pull in GBDK's printf (large -- on the NES it
        # overflows NROM into an unbootable 128 KB banked ROM). Emit them only
        # when text is actually called, not merely imported, so a program that
        # imports graphics.text but conditional-compiles all its text away (e.g.
        # the game slice on the NES) links no printf. Scanned on the
        # post-conditional-compilation AST, so pruned-branch text doesn't count.
        # A custom-font swap (text.set_font / text.set_font_at) emits an extra
        # prelude helper. Track each verb separately so a program that only
        # prints text stays byte-identical (each helper is emitted ONLY when its
        # verb is actually called); font_swap_used is the OR, and it drives the
        # SMS switch from printf to tile-plotted text (a relocated font base
        # can't be read by printf -- see _emit_prelude_gbdk). A font swap
        # implies text_used (it needs the font base / init), so it's ORed in.
        self.font_set_used = self._program_uses_call(program, 'text', 'set_font')
        self.font_at_used = self._program_uses_call(program, 'text',
                                                    'set_font_at')
        self.font_swap_used = self.font_set_used or self.font_at_used
        # text.fill_box (the cc65 filled/bordered overlay box) emits an extra
        # prelude helper; gate it on actual use so a program that never calls it
        # is byte-identical (golden-pinned).
        self.fill_box_used = self._program_uses_call(program, 'text', 'fill_box')
        # text.to_window / text.to_bkg -- route UI text (the vm.core boxes/menus)
        # onto the GB window layer (0x9C00) instead of the scrolling bkg map, GB
        # Studio's overlay model. Emitted only when actually called, so every
        # non-VM program stays byte-identical (golden-pinned). A no-op on consoles
        # without a GB-style window (SMS/GG/NES/Lynx/PCE) -- graceful degradation
        # like platform.sound, so the shared vm.core UI code calls it unconditionally.
        self.text_window_used = (self._program_uses_call(program, 'text', 'to_window')
                                 or self._program_uses_call(program, 'text', 'to_bkg')
                                 or self._program_uses_call(program, 'text', 'window_active'))
        # text.win_sprite_cut -- hide sprites below the window overlay's first
        # scanline (an LYC interrupt), so an actor standing low in the room does
        # not draw OVER an open dialogue box. GB-family only, gated on use.
        self.win_cut_used = self._program_uses_call(program, 'text',
                                                    'win_sprite_cut')
        # text.win_overlay_cut -- the OVERLAY SCANLINE CUTOFF (W7d phase 2, GB
        # Studio's `overlay_cut_scanline`): at that line the window layer goes
        # off and sprites come back, so an overlay covers only the TOP of the
        # screen. The THIRD tenant of LYC_REG. GB-family only, gated on use,
        # and it is the flag that decides whether the stop list carries the
        # extra action bit at all -- everything it adds sits behind it, so a
        # program that never calls it is byte-identical.
        self.overlay_cut_used = self._program_uses_call(program, 'text',
                                                        'win_overlay_cut')
        # bkg.parallax* -- the reference engine's scanline parallax bands (an LYC/STAT
        # interrupt per band). GB-family only, gated on use, and it TAKES OVER
        # the LYC chain that win_sprite_cut would otherwise arm (one register,
        # two consumers - the reference engine installs exactly one LCD ISR per scene).
        self.parallax_used = any(
            self._program_uses_call(program, 'bkg', n)
            for n in ('parallax', 'parallax_band',
                      'parallax_scx', 'parallax_scy'))
        # bkg.raster* -- the per-scanline scroll table (gbdk_raster.py). On the
        # GB family it installs a RAW handler on the STAT vector, which GBDK's
        # add_LCD chain (parallax bands, the text window cuts) cannot share.
        self.raster_used = any(
            self._program_uses_call(program, 'bkg', n)
            for n in ('raster', 'raster_set', 'raster_copy', 'raster_show',
                      'raster_get', 'raster_curve_start', 'raster_curve',
                      'raster_stripes'))
        if (self.raster_used and self.caps.get('has_gb_regs')
                and (self.parallax_used or self.win_cut_used
                     or self.overlay_cut_used)):
            raise RuntimeError(
                "bkg.raster* cannot be combined with bkg.parallax*, "
                "text.win_sprite_cut or text.win_overlay_cut on the Game Boy "
                "family: the scanline table owns the STAT interrupt vector")
        # bkg.set_data_native -- tiles already in the console's own format.
        self.bkg_native_used = self._program_uses_call(program, 'bkg',
                                                       'set_data_native')
        # sprite.plot / drift / hit -- the batch pool verbs (gbdk_batch.py).
        self.batch_used = any(
            self._program_uses_call(program, 'sprite', n)
            for n in ('plot', 'drift', 'hit_box', 'hit'))
        # system.cpu_fast -- the Game Boy Color's double-speed mode.
        self.cpu_fast_used = self._program_uses_call(program, 'system',
                                                     'cpu_fast')
        # video.set_view -- the LETTERBOX offset: a room smaller than the
        # screen is shown centred (the scroll commit subtracts it, every
        # on-screen sprite placement adds it). Real on SMS / Game Gear / PC
        # Engine, a no-op elsewhere; emitted only when called.
        self.view_used = self._program_uses_call(program, 'video', 'set_view')
        # bkg.move -- on the GB register model the scroll write is DEFERRED to
        # v-blank (a shadow committed by gbs_wait_vblank), because the game
        # loop reaches its scroll write ~25-30k cycles after the present
        # returns and v-blank is only 4,560 cycles long: a direct register
        # write lands mid-frame and SHEARS the picture at that scanline (the
        # top at the old camera, the rest at the new - measured on the
        # converted town room as 76 of 300 walking frames torn at LY ~56-66).
        # Gated on use so a program that never scrolls is byte-identical.
        self.bkg_move_used = self._program_uses_call(program, 'bkg', 'move')
        # Only a program that calls BOTH verbs needs the shadow-accumulating
        # bkg.scroll (see _emit_gbdk_scroll_delta); emitting it for every
        # bkg.move program would be dead bytes in the resident image.
        self.bkg_scroll_used = self._program_uses_call(program, 'bkg', 'scroll')
        # text.plot_tile -- a raw tile write routed through the TEXT layer (the
        # window map when to_window is live on the GB family, else the bkg/name
        # table): the custom-frame seam (the reference engine plots its 9-slice frame into
        # the window map the same way). One small prelude helper, gated on
        # actual use so every non-user stays byte-identical (golden-pinned).
        self.plot_tile_used = self._program_uses_call(program, 'text', 'plot_tile')
        # text.glyph_buffer(base, count) -- the GLYPH-BUFFER text mode (G16):
        # the font stays in ROM and each character is rasterized on demand into
        # a reserved high tile band, instead of 96 glyph tiles sitting in VRAM
        # for the whole run. That is what lets a scene use nearly the whole tile
        # table (the reference engine's model: art 0..190, glyphs 204..255) and it retires
        # the SMS/GG set_font_at escape. Gated on the CALL, so every existing
        # program keeps the resident-font path and stays byte-identical
        # (golden-pinned). A no-op on the NES (glyphs come from CHR) and on the
        # cc65 consoles (TGI/conio text draws no tiles).
        self.glyph_buffer_used = self._program_uses_call(program, 'text',
                                                         'glyph_buffer')
        # text.vwf_* -- the VARIABLE-WIDTH renderer (vwf-text-plan V4). Gated on
        # the CALL like every other text verb, so a program that does not use it
        # is byte-identical; it rides the glyph buffer's tile band as a linear
        # RING rather than that mode's per-glyph cache (a variable-width glyph
        # straddles cell boundaries, so there is nothing to dedupe).
        self.vwf_used = (self._program_uses_call(program, 'text', 'vwf_start')
                         or self._program_uses_call(program, 'text', 'vwf_nl')
                         or self._program_uses_call(program, 'text', 'vwf_glyph')
                         or self._program_uses_call(program, 'text', 'vwf_number'))
        # video.set_overlay -- the Lynx PRESENT-time UI overlay hook (vm.core
        # draws an open box/menu INSIDE gbs_present, after the recomposite and
        # before the flip, so UI over a moving background never strobes).
        # Gated on use; a graceful no-op off the Lynx TGI backend.
        self.overlay_used = self._program_uses_call(program, 'video', 'set_overlay')
        self.text_used = (self._program_uses_call(program, 'text', 'print_string')
                          or self._program_uses_call(program, 'text', 'print_number')
                          or self._program_uses_call(program, 'text', 'clear_area')
                          or self.font_swap_used
                          or self.text_window_used
                          or self.plot_tile_used
                          or self.glyph_buffer_used
                          or self.vwf_used
                          or self.win_cut_used
                          or self.overlay_cut_used)
        if self.framework == 'cc65':
            self.cc65_profile = self.CC65_PROFILES.get(
                canonical_platform(self.platform), self.CC65_PROFILES['lynx'])
            stdlib = dict(self.STDLIB_CALLS_CC65_CORE)
            if self.caps['has_draw']:
                stdlib.update(self.STDLIB_CALLS_CC65_DRAW)
            if self.caps['has_sprites']:
                stdlib.update(self.STDLIB_CALLS_CC65_SPRITE)
                if canonical_platform(self.platform) == 'pce':
                    # sprite.font_glyph reads cc65's LINKED PCE console font
                    # (pce_font). The Lynx links no such font, so there it is
                    # the clear "not supported on target" error.
                    stdlib[('sprite', 'font_glyph')] = 'gbs_sprite_font_glyph'
            if self.caps['has_bkg']:
                stdlib.update(self.STDLIB_CALLS_CC65_BKG)
            stdlib.update(self.STDLIB_CALLS_CC65_PALETTE)
            # The Lynx bkg engine costs ~21 KB of RAM (the composited
            # 256x256 background sprite + the bkg tile table), so the cc65
            # preludes emit the bkg engine only for programs that import
            # graphics.bkg -- everything else keeps its memory map (and its
            # golden snapshot) unchanged.
            self.cc65_bkg_imported = any(
                imp.module_name == 'graphics.bkg'
                for module in program.modules for imp in module.imports)
            # A program that imports engine.scroll streams a WIDE (> 256 px) level
            # by rewriting ONE bkg column every 8 px of scroll. The Lynx has no
            # tilemap hardware -- its default bkg engine draws screen-spanning ROW
            # strips and marks them all stale on every set_tiles, so per-frame
            # column streaming thrashes it to a near-blank screen. When a wide
            # level is detected the Lynx engine switches to a transposed COLUMN
            # strip layout (see _emit_cc65_bkg_engine_wide), where one streamed
            # set_tiles recomposites exactly one strip. Gates only the Suzy
            # (Lynx) bkg path; non-wide programs and the PCE are byte-identical.
            self.cc65_wide_scroll = self.cc65_bkg_imported and any(
                imp.module_name == 'engine.scroll'
                for module in program.modules for imp in module.imports)
        else:
            self.cc65_profile = None
            self.cc65_bkg_imported = False
            self.cc65_wide_scroll = False
            stdlib = dict(self.STDLIB_CALLS_GBDK)
            # On GBDK, sprite.set_tile/set_prop normally lower straight to the
            # GBDK macros. When metasprites are in play they must be meta-aware
            # (apply to / reorder the reserved child slots), so route them
            # through the gbs_ wrappers instead. Only swapped when set_meta is
            # used, so ordinary sprite programs are byte-identical.
            if self.platform in ('gameboy_color', 'analogue_pocket'):
                # bkg.set_attrs IS GBDK's set_bkg_attributes on the CGB class
                # (same signature: VRAM bank 1 + set_bkg_tiles), so lower onto
                # it directly rather than through a wrapper whose every byte
                # would sit in the resident image.
                stdlib[('bkg', 'set_attrs')] = 'set_bkg_attributes'
            if (self.platform not in ('sms', 'gamegear')
                    or self.palette_fade_used):
                # palette.load_native is the z80 port's flat 16-entry CRAM
                # run; nowhere else has one, so it degrades to a no-op there
                # (emitted only when called, which the transpiler's platform
                # fork means is never on those consoles).
                # ON SMS/GG it is the ONE CRAM writer a generated coloured game
                # uses, so a program that FADES needs it routed through a real
                # helper (record + scale) instead of straight onto the port's
                # set_palette. Without a fade it keeps the direct lowering and
                # is byte-identical.
                stdlib[('palette', 'load_native')] = 'gbs_pal_native'
            if self.metasprite_used:
                stdlib[('sprite', 'set_tile')] = 'gbs_set_sprite_tile'
                stdlib[('sprite', 'set_prop')] = 'gbs_set_sprite_prop'
            elif self.palette_imported and self._spr_pal_mask() is not None:
                # No metasprites, but the program colours sprites: set_prop
                # still has to leave the palette bits alone (a flip would
                # otherwise reset a coloured sprite to palette 0), so route it
                # through the merging wrapper. Programs that never touch
                # palettes -- and SMS/GG, whose sprites share one palette, so
                # there is nothing to preserve and no wrapper is emitted --
                # keep the bare GBDK macro and stay byte-identical.
                stdlib[('sprite', 'set_prop')] = 'gbs_set_sprite_prop'
            # SMS/Game Gear: the VDP name table is 32x28, but mosaik maps follow
            # the Game Boy's 32x32. Writing 32 rows runs the tilemap copy off the
            # end of the name table into the SAT, which corrupts the *displayed*
            # background (e.g. projects/background's house roof rendered blank).
            # Clamp every bkg-tilemap write to 28 rows -- those extra rows can't
            # be shown on the SMS anyway (vertical scroll wraps at 28).
            if self.platform in ('sms', 'gamegear'):
                stdlib[('bkg', 'set_tiles')] = 'gbs_set_bkg_tiles'
                # 4bpp BACKGROUND tier: a load_bkg16 program feeds a native
                # 4bpp packed-nibble tileset (the scenes.mos `if platform` fork),
                # so route bkg.set_data through a mosaik wrapper that converts to
                # SMS/GG VDP planar + uploads verbatim (bypassing GBDK's 2bpp
                # expansion). Non-4bpp programs keep GBDK's set_bkg_data (byte-
                # identical).
                if self.load_bkg16_used:
                    stdlib[('bkg', 'set_data')] = 'gbs_set_bkg_data'
            if self.bkg_move_used and (self.caps.get('has_gb_regs')
                                       or self.platform in ('sms', 'gamegear')):
                # THE SCROLL WRITE IS DEFERRED TO V-BLANK on the GB register
                # model: bkg.move writes a SHADOW and gbs_wait_vblank commits
                # it right after vsync() returns (the start of v-blank), so a
                # scroll change can never land mid-frame and shear the picture
                # at whatever scanline the game loop had reached (see
                # _emit_gbdk_scroll_move). This also subsumes the old
                # gbs_px_move SCROLL ARBITRATION: while parallax bands are
                # armed the LYC chain owns SCX/SCY, so the COMMIT stands down
                # on gbs_px_n (the shadow still records the camera, which is
                # what the next plain room resumes from). Only swapped for a
                # program that calls bkg.move at all, so everything else is
                # byte-identical - and the NES keeps its direct lowering.
                #
                # SMS/GG TAKE IT TOO, for a second reason: their sprite table is
                # copied to the hardware once per frame at the present
                # (sprite.vbl_hold), so a scroll written IMMEDIATELY is a frame
                # AHEAD of the sprites whenever a game frame spans more than one
                # display frame - the background slides out from under every
                # actor while the camera moves and snaps back when it stops
                # (reported from play). Putting the scroll on the same commit
                # clock as the sprites is what keeps them in phase.
                stdlib[('bkg', 'move')] = 'gbs_scroll_move'
                # ...AND `bkg.scroll` WITH IT. GBDK's scroll_bkg adds to the
                # register directly, so leaving it alone gives one register two
                # writers and the commit wins - every delta wiped by the next
                # present (a free-running register-driven sample: SCY pinned
                # at 0 for a whole 900-frame run). Same gate, so a program that never calls
                # bkg.move keeps scroll_bkg and stays byte-identical.
                if self.bkg_scroll_used:
                    stdlib[('bkg', 'scroll')] = 'gbs_scroll_bkg'
        # Auto-size the Lynx row-strip bkg engine's two big BSS arrays from the
        # world's scene data (VM8 games only), now that the framework + bkg
        # flags are known. A no-op for every non-Lynx / non-VM program.
        self._resolve_lynx_bkg_budgets(program)
        # Auto-size the Lynx sprite tile table from the tiles the program uploads.
        self._resolve_sprite_max_tiles(program)
        # ...and, for a residency world on a cc65 console, the busiest room's
        # sprite need (the PC Engine sizes its table + slots from it).
        self._resolve_cc65_sprite_need(program)
        # Place the glyph band ABOVE everything the program uploads as tile
        # DATA, so text.glyph_buffer needs no hand-picked numbers.
        self._resolve_glyph_band(program)
        # SMS/GG only: refuse a background tile upload past id 191, where the
        # pattern area ends and the name table + SAT begin.
        self._check_smsgg_bkg_range(program)
        # Drop capability-gated calls the target lacks so they raise the clear
        # unsupported-on-target diagnostic instead of failing at link time.
        for cap, calls in (('has_window', self.CALLS_NEEDING_WINDOW),
                           ('has_bkg', self.CALLS_NEEDING_BKG),
                           ('has_sprites', self.CALLS_NEEDING_SPRITES),
                           ('has_sound', self.CALLS_NEEDING_SOUND),
                           ('has_save', self.CALLS_NEEDING_SAVE),
                           ('has_tile_palettes',
                            self.CALLS_NEEDING_TILE_PALETTES)):
            if not self.caps[cap]:
                for key in calls:
                    stdlib.pop(key, None)
        self.stdlib_calls = stdlib

        # ROM banking: collect bank(N) placements and decide whether they are
        # real on this console (GB family) or ignored (everywhere else --
        # portability: one source with banked GB code still builds for the
        # other consoles, whose images are fixed-size/linear).
        self.bank_units = {}
        banked = [(module, decl) for module in program.modules
                  for decl in module.declarations
                  if isinstance(decl, FunctionDecl) and decl.bank > 0]
        for _module, func in banked:
            if func.name == 'main':
                raise RuntimeError(
                    "main() cannot be placed in a ROM bank (bank(%d)): the "
                    "entry point must live in the always-mapped home bank"
                    % func.bank)
        # Build-driven cold-code banking Phase A (`[build] code_banks`): plan
        # the placement -- reserve the code bank above any explicit bank(N)
        # and split address-taken functions into a resident stub + a banked
        # body. Which functions actually bank is decided in Phase B
        # (_apply_code_banks), after _collect_streamed knows which const
        # arrays leave the resident image (those functions must stay home).
        self._plan_code_banks(program, banked)
        self.banking_active = ((bool(banked) or bool(self._code_bank_num))
                               and self._rom_banking_capable())
        self._func_banks = {func.bank for _module, func in banked}
        self._func_banks.update(self._code_bank_of.values())
        if banked and not self._rom_banking_capable():
            print("    Note: bank() placements ignored on %s (no banked-ROM "
                  "support on this console; functions stay in the main bank)"
                  % self.platform)
        self._report_degraded(program)

        # Cross-module symbol tables + C name mangling scheme.
        self._collect_modules(program)

        # Discover user-defined types up front so they can be referenced
        # regardless of declaration order. Type and enum names are
        # program-global (they are never mangled), so in a multi-module build
        # two modules must not declare the same one.
        seen_variants = {}  # variant name -> module that declared it
        for module in program.modules:
            for decl in module.declarations:
                if isinstance(decl, TypeDecl):
                    if self.multi_module and (decl.name in self.struct_types
                                              or decl.name in self.enum_types):
                        raise RuntimeError(
                            "type '%s' (module \"%s\") is already defined by "
                            "another module; type names are program-global"
                            % (decl.name, module.name))
                    if isinstance(decl.type_def, StructType):
                        self.struct_types[decl.name] = decl.type_def
                    elif isinstance(decl.type_def, EnumType):
                        self.enum_types.add(decl.name)
                        # Enum VARIANTS are program-global #defines too: a
                        # duplicate would silently resolve every use to the
                        # last definition, so reject like duplicate types.
                        if self.multi_module:
                            for variant in decl.type_def.variants:
                                other = seen_variants.get(variant.name)
                                if other is not None and other != module.name:
                                    raise RuntimeError(
                                        "enum constant '%s' (module \"%s\") "
                                        "is already defined by module \"%s\"; "
                                        "enum constants are program-global"
                                        % (variant.name, module.name, other))
                                seen_variants[variant.name] = module.name

        # Callbacks (function pointers): assign a typedef per distinct signature
        # and reject references that would capture a banked function's address.
        self._collect_fnptr_typedefs(program)
        self._check_fnptr_targets(program, banked)
        self._check_fnptr_arg_bytes()

        # Asset streaming (Lynx): decide which const arrays leave the resident
        # image for the cart archive (must run before data emission strips them).
        self._collect_streamed(program)

        # Cold-code banking Phase B: now that the streamed symbols are known,
        # place the candidate functions -- a function whose body switches the
        # ROM window stays home (and gets the bank-neutrality wrapper).
        self._apply_code_banks(program)

        # A streamed POINTER is valid only while its bank is mapped: refuse
        # one that flows into a callee of another bank (review E-6). After
        # every bank assignment is final.
        self._check_streamed_args(program)

        # Same-bank call de-trampolining: a module-private local whose callers
        # all share its bank is emitted without BANKED (direct near calls).
        # Runs after every bank assignment above is final.
        self._collect_near_bank_locals(program)

        # Which metasprite-family helpers the MAIN TU itself calls (per-TU
        # gated emission; each bank TU computes its own set in
        # _emit_bank_units). GBDK only - cc65 has its own sprite layer.
        if self.framework == 'gbdk':
            main_funcs = [d for m in program.modules for d in m.declarations
                          if isinstance(d, FunctionDecl)
                          and not (self.banking_active and d.bank > 0)]
            self._tu_meta_needs = self._meta_helper_needs(main_funcs,
                                                          main_tu=True)
        else:
            self._tu_meta_needs = None

        self._emit_prelude()
        self._emit_assets()
        self._emit_stream_runtime()
        self._emit_fnptr_typedefs()
        self._emit_fnptr_value_protos(program)

        if not self.multi_module:
            for module in program.modules:
                self._emit_module(module)
        else:
            # Whole-program layout: every module's types, then every module's
            # data (consts/globals) and function prototypes, then all function
            # bodies -- so cross-module references are always declared before
            # use regardless of module order.
            for module in program.modules:
                self._enter_module(module)
                self._emit_module_types(module)
            for module in program.modules:
                self._enter_module(module)
                self.emit("/* Module: %s */" % module.name)
                self.emit("")
                self._emit_module_data(module)
            for module in program.modules:
                self._enter_module(module)
                self._emit_module_functions(module)

        if self.framework == 'cc65':
            # cc65 (the PC Engine): ONE translation unit. Banked bodies were
            # emitted in place under their bank's segment pragmas; the banked
            # DATA is defined here, and the trampoline is an assembly unit the
            # build links beside the C (see cc65_bank).
            if self._cc65_banking:
                self._cc65_func_banks_used = {
                    d.bank for m in program.modules for d in m.declarations
                    if isinstance(d, FunctionDecl) and d.bank > 0}
                self._emit_cc65_data_banks()
                self.asm_units['bank'] = self._cc65_bank_asm()
                self.cc65_max_bank = self._cc65_max_bank()
                self.cc65_used_banks = self._cc65_used_banks()
        else:
            if self.banking_active:
                # Recollect: Phase B (code_banks) placements happened after
                # the explicit bank(N) collection above.
                banked = [(module, decl) for module in program.modules
                          for decl in module.declarations
                          if isinstance(decl, FunctionDecl) and decl.bank > 0]
                if banked:
                    self._emit_bank_units(program, banked)
            if self._stream_mode == 'gb' or self.prelude_bank_defs:
                self._emit_data_bank_units()

        # The AST may be rewritten before the next generate(); a stale set
        # would answer about a program that no longer exists.
        self._called_verbs = None
        self._called_verbs_for = None
        return "\n".join(self.output)
