"""mosaik_scenes.base - SceneError + shared identifier/array/animated-tile emit helpers."""


class SceneError(Exception):
    pass


def _ident(name):
    """Uppercase C-ish identifier stem from a scene/kind name."""
    out = "".join(c if c.isalnum() else "_" for c in str(name)).upper()
    if not out or out[0].isdigit():
        out = "S_" + out
    return out


def _script_ident(name):
    """The identifier stem `mosaik_vm.py` uses for a script's `ENTRY_<x>` const
    (LOWERCASE, kept in lockstep with mosaik_vm._ident). A slot ref in world.toml
    is a script NAME; the emitted selector references `scripts.ENTRY_<that>`, so
    the two must agree on the mangled stem."""
    out = "".join(c if c.isalnum() else "_" for c in str(name)).lower()
    if not out or out[0].isdigit():
        out = "s_" + out
    return out
def _flatten_map(raw, w, h, scene, what="map"):
    """Accept `map`/`collision` as a flat list of w*h ints, or h rows of w."""
    if raw and isinstance(raw[0], list):
        rows = raw
        if len(rows) != h or any(len(r) != w for r in rows):
            raise SceneError("scene '%s': %s must be %d rows of %d"
                             % (scene, what, h, w))
        flat = [v for row in rows for v in row]
    else:
        flat = list(raw)
    if len(flat) != w * h:
        raise SceneError("scene '%s': %s has %d cells, expected %d"
                         % (scene, what, len(flat), w * h))
    return [int(v) & 0xFF for v in flat]


def _emit_array(lines, c_type_name, name, n, values, per_line=16):
    lines.append("    const %s: array[%s, %d] = [" % (name, c_type_name, n))
    for i in range(0, len(values), per_line):
        chunk = ", ".join(str(v) for v in values[i:i + per_line])
        tail = "," if i + per_line < len(values) else ""
        lines.append("        %s%s" % (chunk, tail))
    lines.append("    ]")


def _emit_reader(lines, fn, name, ret_type, idx_type="u8"):
    """A read accessor for one const table, so this module's own code is the
    array's ONLY reader.

    Under `[build] code_banks` the generated `rooms.mos` sits in a different ROM
    bank, and **a const array read from another bank's code is pinned RESIDENT**
    (bank 0) - the same trap that cost the colour tier 2,771 B until the palette
    words were read from `scenes` instead of `rooms`. Going through a function
    here lets the data co-locate into this module's bank. Every call site is on
    the cold room-load path, so a BANKED call per read costs nothing that
    matters. Emitted for VM worlds only (a hand-written game still indexes the
    exported array directly, and its output stays byte-identical).

    It only pays if the module's OWN readers are banked too: `SCENE_W`/`SCENE_H`
    look identical and moved 0 B, because `paint` and `warm` read them and are
    themselves resident (they hold the streaming seam reads). Check where an
    array's other readers live before adding a reader for it.
    """
    lines.append("    function %s(i: %s) -> %s {" % (fn, idx_type, ret_type))
    lines.append("        return %s[i]" % name)
    lines.append("    }")


#: Cap on the display-frame delta an animation catches up in one tick. After a
#: room load or a long blocking op the delta is huge, and replaying it would
#: fast-forward the animation; dropping the excess loses time instead. The same
#: reasoning (and the same number) as vm.core's music catch-up.
_ANIM_CATCHUP_MAX = 8


def _emit_scene_animated(lines, scene_anims, scenes, tiles=None,
                         has_tile_pal=False):
    """Per-SCENE background tile animation (`[[scene.animated_tile]]`).

    Same trick as the world-global form - the map keeps the tile INDEX and the
    tile's DATA is rewritten on a timer, so every cell using that index
    animates at once - but scoped to one room, which is what lets it combine
    with per-scene tilesets / stream / paint_table. The reference engine's
    `vm_replace_tile_xy` is exactly this: it reads the map's tile index at
    (x, y) and writes 16 bytes of tileset data at that index.

    **Animating a SHARED index animates every cell using it, and that is the
    feature, not a bug.** Measured on the reference ROM's own tilemap, the
    sample town's flower tile is shared by 59 cells and the reference engine animates all
    59; splitting the animated cell onto a private index would DIVERGE from it.

    The frames are baked here from their own image, so an animation adds
    nothing to the scene's uploaded tileset and costs no background VRAM.
    Emitted as `anim_tick_at(scene)` - the generated rooms.mos passes the room
    it loaded - and only when some scene has one, so every existing world keeps
    the argument-less `anim_tick()` alone and stays byte-identical."""
    specs = []                    # (scene index, [(i, tile, count, period, frames)])
    n = 0
    for si, rows in enumerate(scene_anims or []):
        got = []
        for a, src in rows or []:
            tile = int(a["tile"]) & 0xFF
            count = max(1, int(a.get("count", 1)))
            period = max(1, int(a.get("period", 12)))
            frames = [int(f) for f in a.get("frames", [])]
            name = (scenes[si].get("name") if si < len(scenes) else si)
            if len(frames) < 2:
                raise SceneError("scene '%s': [[animated_tile]] for tile %d "
                                 "needs >= 2 frames" % (name, tile))
            # No `png` and no per-scene tileset: index the world's shared one,
            # which is resolved HERE rather than in the context because the
            # ranked-palette path rebuilds `tiles` after the scenes are read.
            if not src:
                src = tiles
            if not src:
                raise SceneError("scene '%s': [[animated_tile]] for tile %d "
                                 "has no frame source (give it a `png`, or a "
                                 "tileset to index)" % (name, tile))
            have = len(src) // 16
            for f in frames:
                if f < 0 or f + count > have:
                    raise SceneError(
                        "scene '%s': [[animated_tile]] frame source tile %d "
                        "(+%d) is outside its %d-tile image"
                        % (name, f, count, have))
            if tile + count > 256:
                raise SceneError("scene '%s': [[animated_tile]] tile %d (+%d) "
                                 "overflows the 256-tile background table"
                                 % (name, tile, count))
            got.append((n, tile, count, period, frames, src))
            n += 1
        if got:
            specs.append((si, got))
    if not specs:
        return False

    lines.append("    -- PER-SCENE background tile animations: the map keeps the")
    lines.append("    -- tile INDEX and its DATA is rewritten on a timer, so every")
    lines.append("    -- cell using that index animates at once (the reference engine's")
    lines.append("    -- vm_replace_tile_xy, and its own ROM animates a shared tile")
    lines.append("    -- across every cell too). Frames are baked from their own")
    lines.append("    -- image, so this costs no background VRAM.")
    for _si, got in specs:
        for i, tile, count, period, frames, src in got:
            lines.append("    const SAN%d_TILE: u8 = %d" % (i, tile))
            lines.append("    const SAN%d_COUNT: u8 = %d" % (i, count))
            lines.append("    const SAN%d_PERIOD: u8 = %d" % (i, period))
            lines.append("    const SAN%d_FRAMES: u8 = %d" % (i, len(frames)))
            for k, f in enumerate(frames):
                block = list(src[f * 16:(f + count) * 16])
                _emit_array(lines, "u8", "SAN%d_F%d" % (i, k), len(block), block)
            # initialiser-free: a module-level `= 0` rides the boot INITIALIZER,
            # which is resident even when this module banks.
            lines.append("    var san%d_phase: u8" % i)
            lines.append("    var san%d_timer: u8" % i)
            lines.append("")
    # The room a tick belongs to, plus the RE-SEED latch: a room load re-uploads
    # the scene's tileset, which puts the STATIC pixels back under the animated
    # index, so the phase has to restart and frame 0 has to be re-written.
    lines.append("    var san_room: u8")
    lines.append("    var san_seeded: u8")
    lines.append("    var san_last: u8")
    lines.append("")
    # THE FRAME'S PALETTE, on the two consoles that need one told.
    #
    # SMS/GG store every background tile at 4bpp and the port's 2bpp upload
    # decides which 4 CRAM entries it lands on. The scene TILESET upload passes
    # a slot PER TILE (bkg.set_data_pal); an animated FRAME goes through the
    # plain bkg.set_data, which uses the port's `_current_2bpp_palette` - so
    # without this every frame landed on palette 0 and the animation rendered
    # in the wrong colours while the static art around it was right (the
    # converted sample's waterfall, flowers and cave mouth; reported from play
    # 2026-08-25). This is the same mechanism the SPRITE path already uses:
    # a whole upload shares one slot, so set the port's map and upload.
    #
    # RESTORED afterwards, because the map is global state and the next plain
    # background upload would otherwise inherit an animated tile's palette.
    # Gated on the world having per-tile palettes at all - `tile_pal` only
    # exists then - and folded away entirely on every other console.
    if has_tile_pal:
        lines.append('    if platform == "sms" or platform == "gamegear" {')
        lines.append("    const SAN_PAL2BPP: array[u16, 4] = "
                     "[ 0x3210, 0x7654, 0xBA98, 0xFEDC ]")
        lines.append("    local function san_pal(t: u8) {")
        lines.append("        palette.set_2bpp(SAN_PAL2BPP[tile_pal(san_room, t) & 3])")
        lines.append("    }")
        lines.append("    local function san_pal_off() {")
        lines.append("        palette.set_2bpp(SAN_PAL2BPP[0])")
        lines.append("    }")
        lines.append("    } else {")
        lines.append("    local function san_pal(t: u8) {")
        lines.append("    }")
        lines.append("    local function san_pal_off() {")
        lines.append("    }")
        lines.append("    }")
    else:
        lines.append("    local function san_pal(t: u8) {")
        lines.append("    }")
        lines.append("    local function san_pal_off() {")
        lines.append("    }")
    lines.append("")
    lines.append("    -- Called once per frame with the room the shell loaded.")
    lines.append("    --")
    lines.append("    -- IT STEPS BY ELAPSED DISPLAY FRAMES, not once per call, and")
    lines.append("    -- that is what makes the period mean what the reference engine authored.")
    lines.append("    -- Its timer counts LCD frames; this is called once per VM")
    lines.append("    -- frame, which is 1 to 3 LCD frames depending on how busy the")
    lines.append("    -- room is - measured on the converted sample town (a heavy")
    lines.append("    -- 56x56 room) a per-call counter ran the waterfall at 45 LCD")
    lines.append("    -- frames a step against the reference ROM's 30. A decoration")
    lines.append("    -- has no game balance to preserve, so unlike a converted")
    lines.append("    -- VELOCITY it should not ride the VM clock at all; this is the")
    lines.append("    -- same rule the overlay curtain and the music catch-up follow.")
    lines.append("    function anim_tick_at(scene: u8) {")
    lines.append("        var fnow: u8 = system.frames()")
    lines.append("        var fd: u8 = fnow - san_last")
    lines.append("        san_last = fnow")
    lines.append("        if fd > %d {" % _ANIM_CATCHUP_MAX)
    lines.append("            fd = %d" % _ANIM_CATCHUP_MAX)
    lines.append("        }")
    lines.append("        if scene != san_room {")
    lines.append("            san_room = scene")
    lines.append("            san_seeded = 0")
    lines.append("        }")
    for si, got in specs:
        lines.append("        if scene == %d {" % si)
        lines.append("            if san_seeded == 0 {")
        for i, _t, _c, _p, _f, _s in got:
            lines.append("                san%d_phase = 0" % i)
            lines.append("                san%d_timer = 0" % i)
            lines.append("                san_pal(SAN%d_TILE)" % i)
            lines.append("                bkg.set_data(SAN%d_TILE, SAN%d_COUNT, SAN%d_F0)"
                         % (i, i, i))
            lines.append("                san_pal_off()")
        lines.append("                san_seeded = 1")
        lines.append("            }")
        for i, _t, _c, period, frames, _s in got:
            lines.append("            san%d_timer += fd" % i)
            lines.append("            while san%d_timer >= SAN%d_PERIOD {" % (i, i))
            lines.append("                san%d_timer -= SAN%d_PERIOD" % (i, i))
            lines.append("                san%d_phase += 1" % i)
            lines.append("                if san%d_phase >= SAN%d_FRAMES {" % (i, i))
            lines.append("                    san%d_phase = 0" % i)
            lines.append("                }")
            lines.append("                switch san%d_phase {" % i)
            for k in range(len(frames)):
                label = "default" if k == len(frames) - 1 else "case %d" % k
                lines.append("                    %s { san_pal(SAN%d_TILE) "
                             "bkg.set_data(SAN%d_TILE, SAN%d_COUNT, SAN%d_F%d) "
                             "san_pal_off() }" % (label, i, i, i, i, k))
            lines.append("                }")
            lines.append("            }")
        lines.append("        }")
    lines.append("    }")
    lines.append("")
    return True


def _emit_replace_tiles(lines, bank, has_tile_pal=False):
    """The world's REPLACEMENT TILE BANK: `replace_tile(room, dst, src)`.

    The runtime half of the reference engine's `VM_REPLACE_TILE` (its
    `EVENT_REPLACE_TILE_XY`), which the VM8 `BKG_TILE` / `BKG_TILE_E` opcodes
    route through `core.set_bkg_tile`. It rewrites the 16 bytes of background
    tile `dst` from a baked bank of replacement art, so every map cell holding
    that index redraws at once.

    That indirection is the whole point, and it is why this is a DATA write
    rather than a map write: the map is const and gets repainted on every
    scroll and every room warm-up, so a changed cell would be undone, while
    changed tile PIXELS survive both. It is the same mechanism the animated
    tiles use - the difference is only that a script chooses the frame, and
    usually COMPUTES it (a wallet readout writes `gold % 100 / 10` under each
    of four cells).

    `bank` is the concatenated 2bpp tile data, `src` its tile index. Out of
    range is a no-op rather than a wild read: `src` reaches here from the
    expression stack, so a script's arithmetic - not the world author - decides
    it, and a converted readout really does compute an out-of-range digit while
    the value is still being narrowed.

    The bank is baked here from its own images, so it costs no background VRAM
    and nothing uploads it; only the 16 bytes a write actually names reach the
    hardware. Emitted only for a world that HAS a bank, so every other world
    stays byte-identical.
    """
    n = len(bank) // 16
    if not n:
        return False
    lines.append("    -- REPLACEMENT TILE BANK (the reference engine's VM_REPLACE_TILE): the")
    lines.append("    -- pixels a script may write into a background tile. Baked from")
    lines.append("    -- its own art, so it costs no background VRAM - only the 16")
    lines.append("    -- bytes a write names ever reach the hardware. The map keeps")
    lines.append("    -- its index, which is what makes the change survive a scroll")
    lines.append("    -- and a repaint (a changed MAP cell would not).")
    lines.append("    const RTILE_COUNT: u8 = %d" % min(255, n))
    _emit_array(lines, "u8", "RTILES", len(bank), list(bank))
    # A 16-byte staging buffer, because bkg.set_data takes a whole array and
    # the source is one tile inside a much longer one - there is no offset
    # form. 16 B of RAM against a switch arm per tile in ROM.
    lines.append("    var rt_buf: array[u8, 16]")
    lines.append("")
    # THE DESTINATION TILE'S PALETTE, on the two consoles that need telling.
    # Same mechanism (and same reason) as the animated tiles: SMS/GG store
    # every background tile at 4bpp, so the 2bpp upload decides which 4 CRAM
    # entries it lands on, and a plain bkg.set_data would put the new pixels on
    # palette 0 while the art around them keeps the scene's. Restored after,
    # because the port's map is global state.
    if has_tile_pal:
        lines.append('    if platform == "sms" or platform == "gamegear" {')
        lines.append("    const RT_PAL2BPP: array[u16, 4] = "
                     "[ 0x3210, 0x7654, 0xBA98, 0xFEDC ]")
        lines.append("    local function rt_pal(room: u8, t: u8) {")
        lines.append("        palette.set_2bpp(RT_PAL2BPP[tile_pal(room, t) & 3])")
        lines.append("    }")
        lines.append("    local function rt_pal_off() {")
        lines.append("        palette.set_2bpp(RT_PAL2BPP[0])")
        lines.append("    }")
        lines.append("    } else {")
        lines.append("    local function rt_pal(room: u8, t: u8) {")
        lines.append("    }")
        lines.append("    local function rt_pal_off() {")
        lines.append("    }")
        lines.append("    }")
    else:
        lines.append("    local function rt_pal(room: u8, t: u8) {")
        lines.append("    }")
        lines.append("    local function rt_pal_off() {")
        lines.append("    }")
    lines.append("")
    lines.append("    -- Write replacement tile `src` over background tile `dst`.")
    lines.append("    -- core.set_bkg_tile registers this; the BKG_TILE ops call it.")
    lines.append("    function replace_tile(room: u8, dst: u8, src: u8) {")
    lines.append("        if src >= RTILE_COUNT {")
    lines.append("            return")
    lines.append("        }")
    lines.append("        var b: u16 = src")
    lines.append("        b = b * 16")
    lines.append("        var i: u8 = 0")
    lines.append("        while i < 16 {")
    lines.append("            rt_buf[i] = RTILES[b + i]")
    lines.append("            i += 1")
    lines.append("        }")
    lines.append("        rt_pal(room, dst)")
    lines.append("        bkg.set_data(dst, 1, rt_buf)")
    lines.append("        rt_pal_off()")
    lines.append("    }")
    lines.append("")
    return True


def _emit_animated(lines, anims, tiles, tile_count):
    """Emit background tile-data animation into the scenes module (the reference-engine
    "animate background tiles" trick): a tile keeps a fixed INDEX in the map, but
    its pixel DATA is rewritten on a timer, so every cell using it animates at
    once. Each `[[animated_tile]]` in the world TOML gives `tile` (the map index
    whose data is swapped), `frames` (source tile indices in the tileset whose
    bytes each frame uploads), `period` (ticks/frame), and optional `count`
    (tiles per frame, default 1).

    Emits a self-contained `anim_tick()` the game loop calls once per frame -- an
    inline timer per tile, so `bkg.set_data` fires only when the frame advances
    (the change-guard is implicit), and no `engine.anim` dependency. `anim_tick()`
    is **always** emitted (an empty no-op when there are no animated tiles) so the
    caller can export it unconditionally and a loop calling `scenes.anim_tick()`
    keeps compiling after the last animated tile is removed.
    """
    specs = []
    for i, a in enumerate(anims):
        tile = int(a["tile"]) & 0xFF
        count = max(1, int(a.get("count", 1)))
        period = max(1, int(a.get("period", 12)))
        frames = [int(f) for f in a.get("frames", [])]
        if len(frames) < 2:
            raise SceneError("[[animated_tile]] for tile %d needs >= 2 frames"
                             % tile)
        for f in frames:
            if f < 0 or f + count > tile_count:
                raise SceneError("[[animated_tile]] frame source tile %d (+%d) "
                                 "is outside the %d-tile tileset" % (f, count, tile_count))
        if tile + count > 256:
            raise SceneError("[[animated_tile]] tile %d (+%d) overflows the "
                             "256-tile background table" % (tile, count))
        specs.append((i, tile, count, period, frames))

    if specs:
        lines.append("    -- Background tile animations (reference-engine tile-data swap):")
        lines.append("    -- keep the tile INDEX fixed in the map, rewrite its DATA on")
        lines.append("    -- a timer. The game loop calls anim_tick() once per frame.")
    else:
        lines.append("    -- No animated tiles: anim_tick() is a no-op, so the game")
        lines.append("    -- loop can still call scenes.anim_tick() unconditionally.")
    for i, tile, count, period, frames in specs:
        lines.append("    const ANIM%d_TILE: u8 = %d" % (i, tile))
        lines.append("    const ANIM%d_COUNT: u8 = %d" % (i, count))
        lines.append("    const ANIM%d_PERIOD: u8 = %d" % (i, period))
        lines.append("    const ANIM%d_FRAMES: u8 = %d" % (i, len(frames)))
        for k, src in enumerate(frames):
            block = list(tiles[src * 16:(src + count) * 16])
            _emit_array(lines, "u8", "ANIM%d_F%d" % (i, k), len(block), block)
        # No `= 0`: a module-level initializer rides the boot INITIALIZER,
        # which is RESIDENT (bank 0) even when this module banks. BSS is
        # zeroed by the C runtime, so this is the same semantics for free.
        lines.append("    var anim%d_phase: u8" % i)
        lines.append("    var anim%d_timer: u8" % i)
        lines.append("    var anim%d_started: u8" % i)
        lines.append("")
    lines.append("    function anim_tick() {")
    for i, tile, count, period, frames in specs:
        # Seed frame 0 on the first call -- needed when ANIM_TILE is a slot above
        # the static tileset (not uploaded by the initial bkg.set_data), so it
        # never flashes uninitialised data before the first frame step.
        lines.append("        if anim%d_started == 0 {" % i)
        lines.append("            anim%d_started = 1" % i)
        lines.append("            bkg.set_data(ANIM%d_TILE, ANIM%d_COUNT, ANIM%d_F0)"
                     % (i, i, i))
        lines.append("        }")
        lines.append("        anim%d_timer += 1" % i)
        lines.append("        if anim%d_timer >= ANIM%d_PERIOD {" % (i, i))
        lines.append("            anim%d_timer = 0" % i)
        lines.append("            anim%d_phase += 1" % i)
        lines.append("            if anim%d_phase >= ANIM%d_FRAMES {" % (i, i))
        lines.append("                anim%d_phase = 0" % i)
        lines.append("            }")
        lines.append("            switch anim%d_phase {" % i)
        for k in range(len(frames)):
            label = "default" if k == len(frames) - 1 else "case %d" % k
            lines.append("                %s { bkg.set_data(ANIM%d_TILE, ANIM%d_COUNT, ANIM%d_F%d) }"
                         % (label, i, i, i, k))
        lines.append("            }")
        lines.append("        }")
    lines.append("    }")
    lines.append("")
    return True
