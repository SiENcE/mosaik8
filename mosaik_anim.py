#!/usr/bin/env python3
"""mosaik_anim -- transpile MosaiK Studio ``[animations]`` clips into a VM8
``anim`` module the native player/actor handlers animate from.

This is the VM8 half of "our animation system": the studio's per-kind directional
CLIP model (persisted as `studio.toml
[animations.<kind>]`) is DATA -- states (idle / walk / jump / fall), each a set of
frames per facing (down / up / left / right) at a period. This module lowers those
clips (resolving each frame's sprite-cell NAME to its tile offset in a named sheet)
into a generated `anim.mos` that exposes three selectors:

    anim.count(state, facing) -> u8     -- frames in this state+facing (0 = none)
    anim.period(state)        -> u8     -- ticks per frame
    anim.frame(state, facing, i) -> u8  -- the i-th frame's base tile

`vm.player` (opt-in, `player.set_anim`) reads them and drives `engine.anim`, picking
the state from the native movement (idle/walk on the ground, jump/fall in the air)
and the facing from `pface`. State ids are FIXED (idle 0 / walk 1 / jump 2 / fall 3);
facing ids match `vm.player`'s pface (down 0 / up 1 / left 2 / right 3).

The schema mirrors `animation_model.Clip` but is reimplemented here (no studio
import) so a bare-mosaik8 project builds it -- the same engine/studio split as
`mosaik_scenes` reading `world.toml`.

SMS/GG SOFT-FLIP BAKE (review 2026-07-12, item 2.5): a `flip_left` clip derives its
LEFT facing by mirroring RIGHT via hardware FLIP_X. SMS/Game Gear have NO sprite
flip, so that LEFT would render the RIGHT art unflipped. When the caller supplies a
`bake`, the generated module emits a MODULE-SCOPE `if platform == "sms" or
platform == "gamegear"` fork: the SMS/GG branch points those LEFT frames at
pre-mirrored tiles with flip 0, while every other console keeps the FLIP_X path.
Conditional compilation drops the unused branch, so a flip-capable build stays
byte-identical and a no-flip build shows the correct facing. Two layouts:

* boot upload (`build_flip_bake`): the mirror PIXELS are baked into the module
  (`MIRROR`) and `upload_flip()` uploads them above every sheet.
* per-room residency (`residency_flip_bake`, 2026-09-24): each kind's frames are
  RELATIVE to its own sheet, so its mirrors are baked by the BUILD onto the end of
  that sheet's own tile data on a no-flip console (`mosaik8_build`,
  `mosaik_assets.mirror_cell_tiles`) and ride its ordinary upload. The module only
  carries the indices.
"""

STATES = ["idle", "walk", "jump", "fall"]          # fixed state ids 0..3
FACINGS = ["down", "up", "left", "right"]           # facing ids 0..3 == vm.player pface
NF = len(FACINGS)

GB_TILE_BYTES = 16                                  # GB 2bpp: the bake's tile depth


class AnimError(Exception):
    pass


def _directional(clip):
    return any(f in clip for f in FACINGS)


def _first_nonempty_facing(clip):
    for f in FACINGS:
        if clip.get(f):
            return f
    return None


def _first_nonempty(clip):
    if _directional(clip):
        f = _first_nonempty_facing(clip)
        return list(clip[f]) if f else []
    return list(clip.get("frames", []))


def _pal_of(clip, facing, directional):
    """The authored per-frame PALETTE list for one facing (`facing` None = the
    flat clip's own list), or None.

    A scalar (`pal = 6`) is the whole clip on one palette, which is how a GB
    Studio sprite STATE recolours (its exploding mine turns orange); a list is
    one value per frame, which is how it FLASHES (alternating tiles carrying
    OBP0 then OBP1). Both spellings pad by repeating the last value, so a
    scalar and a one-element list mean the same thing."""
    v = None
    if directional and facing:
        v = clip.get("pal_" + facing)
    if v is None:
        v = clip.get("pal")
    if v is None:
        return None
    if isinstance(v, int):
        return [v]
    return [int(x) for x in v]


def _resolve(clip):
    """{facing: ([cell names], flip, [palette per frame])} padded to the clip's
    frame count. An empty facing falls back to the first non-empty one; a
    `flip_left` clip's empty LEFT facing DERIVES from RIGHT with flip=1 (mirrors
    Clip.facing_frames + the composer's hardware-flip lowering). flip is a no-op
    on SMS/GG (no sprite flip) -- the SMS/GG soft-flip bake (build_flip_bake +
    the conditional module) supplies mirrored tiles there instead.

    A palette rides the SOURCE facing, not the asking one: a derived LEFT wears
    RIGHT's colours, because it is RIGHT's frames it is drawing."""
    directional = _directional(clip)
    flip_left = bool(clip.get("flip_left"))
    if directional:
        n = max((len(clip.get(f, [])) for f in FACINGS), default=0)
    else:
        n = len(clip.get("frames", []))
    out = {}
    for f in FACINGS:
        src = f if directional else None
        seq = list(clip.get(f, [])) if directional else list(clip.get("frames", []))
        flip = 0
        if not seq:
            if f == "left" and flip_left and directional and clip.get("right"):
                seq = list(clip.get("right"))     # derive LEFT by mirroring RIGHT
                src = "right"
                flip = 1
            else:
                seq = _first_nonempty(clip)
                src = _first_nonempty_facing(clip)
        if seq:
            while len(seq) < n:
                seq.append(seq[-1])
        seq = seq[:n]
        # 255 = NO authored palette. It cannot be 0: 0 is a real palette, and
        # an uncoloured frame must leave the palette the room load applied
        # (the per-cell map) ALONE rather than write palette 0 over it - which
        # is exactly what flattened every coloured actor in a room the moment
        # one OTHER kind was recoloured (the parallax room's big actor drew through
        # OBJ palette 0 instead of its per-cell 6).
        pal = _pal_of(clip, src, directional) or []
        if pal:
            while len(pal) < len(seq):
                pal.append(pal[-1])
            pal = pal[:len(seq)]
        else:
            pal = [255] * len(seq)
        out[f] = (seq, flip, pal)
    return out


def _arr(name, ty, vals, indent=4):
    pad = " " * indent
    body = ", ".join(str(v) for v in vals)
    return "%sconst %s: array[%s, %d] = [ %s ]" % (pad, name, ty, len(vals), body)


def _build_kind(clips, cell_tile, frames, mirror=None, cell_mask=None,
                masks=None, cell_desc=None, descs=None, pals=None,
                mirror_mask=None):
    """Resolve one kind's `{state -> clip}` into (off, cnt, flip, period) tables,
    appending its frame tiles to the shared `frames` list (offsets index into it).

    With `mirror` (a {cell name -> mirror tile index} map) a flip_left-derived
    LEFT facing is lowered to the mirror tiles with flip 0 (the SMS/GG soft
    flip); without it the LEFT keeps flip=1 and references the RIGHT tiles
    (hardware FLIP_X). `mirror_mask` is the mirrored cells' own blank-column
    masks (a sparse cell mirrors its mask too).

    With `cell_desc` (`{frame name -> (pw, ph, objs)}`, the manifests' [[frame]]
    DESCRIPTOR entries) a clip frame naming one is a per-OBJECT composition
    over the sheet's pool cells rather than a dense strip cell: its FRAMES
    value becomes the frame's TABLE INDEX (masked to u8 - the upload cache's
    identity, not a tile) and `descs` collects the objs in step."""
    f_off = [0] * (len(STATES) * NF)
    f_cnt = [0] * (len(STATES) * NF)
    f_flip = [0] * (len(STATES) * NF)
    period = [8] * len(STATES)
    for si, st in enumerate(STATES):
        clip = clips.get(st)
        if not clip:
            continue
        period[si] = int(clip.get("period", 8))
        res = _resolve(clip)
        for fi, fac in enumerate(FACINGS):
            seq, flip, pal = res[fac]
            baked = mirror is not None and flip == 1 and all(c in mirror for c in seq)
            f_off[si * NF + fi] = len(frames)
            f_cnt[si * NF + fi] = len(seq)
            f_flip[si * NF + fi] = 0 if baked else flip
            for ci, cell in enumerate(seq):
                if pals is not None:
                    pals.append(pal[ci])
                if cell_desc is not None and cell in cell_desc:
                    # a DESCRIPTOR frame: identity for the cache, objs beside
                    frames.append(len(frames) & 0xFF)
                    if masks is not None:
                        masks.append(0)
                    if descs is not None:
                        descs.append(cell_desc[cell])
                    continue
                if baked:
                    frames.append(mirror[cell])
                else:
                    if cell not in cell_tile:
                        raise AnimError("animation frame references unknown sprite cell "
                                        "'%s'" % cell)
                    frames.append(cell_tile[cell])
                if masks is not None:
                    # A mirrored cell holds the same DRAWN columns in reverse
                    # order, so its blank-column mask is the source's, reversed.
                    masks.append((mirror_mask or {}).get(cell, 0) if baked
                                 else (cell_mask or {}).get(cell, 0))
                if descs is not None:
                    descs.append(None)
    return f_off, f_cnt, f_flip, period


def _mirror_cells(clip_dicts):
    """Ordered unique sprite-cell names a flip_left clip DERIVES its LEFT facing from
    (the RIGHT cells that must be pixel-mirrored on a no-sprite-flip console). Empty
    when nothing uses flip_left, so the bake -- and the conditional module -- vanish."""
    seen = []
    for clip in clip_dicts:
        seq, flip, _pal = _resolve(clip)["left"]
        if flip == 1:
            for cell in seq:
                if cell not in seen:
                    seen.append(cell)
    return seen


def build_flip_bake(cells, sheet_pngs):
    """Pixel-mirror each cell in `cells` (from the sheet PNGs, in `[assets] sprites`
    order) into GB 2bpp tiles for the SMS/GG soft-flip bake. Returns
    {"index": {cell -> baked tile index}, "tiles": bytes, "base": total sheet tiles}
    or None when `cells` is empty.

    The baked tiles land ABOVE the sheet's own tiles (`base` = total tiles across the
    sheets = the runtime `<sheet>_tile_count`), so `upload_flip()` uploads them at
    `base` and the LEFT frames index into them. The mirror reuses the asset encoder
    (`shades_to_gb_tiles` on x-reversed shade rows) so the baked tiles match the
    sheet's own 2bpp exactly -- the same technique the retired composer used."""
    if not cells:
        return None
    import mosaik_assets as ma
    loc = {}
    base = 0
    for png in sheet_pngs:
        manifest = ma.load_sprite_manifest(png) or []
        for name, x, y, w, h in manifest:
            loc[name] = (png, x, y, w, h)
        base += sum((w // 8) * (h // 8) for _n, _x, _y, w, h in manifest)
    index = {}
    tiles = bytearray()
    shade_cache = {}
    for cell in cells:
        if cell not in loc:
            raise AnimError("flip_left cell '%s' has no sprite-sheet manifest entry "
                            "(needed for the SMS/GG mirror bake)" % cell)
        png, x, y, w, h = loc[cell]
        if png not in shade_cache:
            shade_cache[png] = ma.png_to_shades(png)
        _w, _h, rows = shade_cache[png]
        rect = ma._slice_rows(rows, x, y, w, h)
        mirrored = [list(reversed(r)) for r in rect]     # horizontal flip
        index[cell] = base + len(tiles) // GB_TILE_BYTES
        tiles += ma.shades_to_gb_tiles(w, h, mirrored)
    return {"index": index, "tiles": bytes(tiles), "base": base}


def _mirror_mask(mask, cols):
    """A blank-column mask as seen in the mirror: column c -> cols - 1 - c."""
    return sum(1 << (cols - 1 - c) for c in range(cols) if (mask >> c) & 1)


def residency_flip_bake(anims, kind_pngs):
    """The SMS/GG soft-flip bake under PER-ROOM sprite residency, where every
    kind's sheet is uploaded at its own per-room VRAM base and the clips FRAMES
    are kind-RELATIVE. The fixed-base boot bake above cannot serve that, so here
    each sheet's mirrors are baked onto the END of that sheet's own tile data -
    by the BUILD, on a console with no sprite flip (`mosaik8_build`) - and ride
    the kind's ordinary upload into whichever room loads it. A mirror's index is
    therefore the sheet's own tile count plus its offset among the mirrors.

    `anims`: {kind -> {state -> clip}}; `kind_pngs`: {kind -> its sheet PNG}
    (studio.toml [kind_sprites]). Only a kind's OWN [[sprite]] cells are
    mirrored; a [[frame]] descriptor composition keeps its FLIP_X frames (drawn
    unmirrored on SMS/GG). Kinds sharing a sheet share its mirrors.

    Returns {"index": {cell -> kind-relative mirror tile}, "mask": {cell ->
    mirrored blank-column mask}, "by_stem": {sheet stem -> {"extra": tiles,
    "cells": [(src tile, w, h), ...]}}} or None when nothing is mirrored. THE
    ONE planner generate_clips (the frame indices), generate_rooms (the grown
    per-kind tile counts) and the build (the mirrored tiles) all call, so the
    three cannot disagree about where a mirror is. No pixels are read here."""
    import mosaik_assets as ma
    per_stem = {}                   # stem -> (sheet defs, ordered mirror cells)
    for kname, clips in anims.items():
        png = kind_pngs.get(kname)
        if not png or not isinstance(clips, dict):
            continue
        stem = ma.asset_c_name(png)
        if stem not in per_stem:
            sparse = ma.sprite_frame_masks(png)
            defs = {n: (off, w, h) + tuple(sparse.get(n, (w, 0)))
                    for n, off, w, h in ma.sheet_sprite_defs(png)}
            per_stem[stem] = (defs, [])
        defs, cells = per_stem[stem]
        for cell in _mirror_cells(c for c in clips.values() if isinstance(c, dict)):
            if cell in defs and cell not in cells:
                cells.append(cell)
    index, masks, by_stem = {}, {}, {}
    for stem, (defs, cells) in per_stem.items():
        if not cells:
            continue
        dst = max(off + w * h for off, w, h, _c, _m in defs.values())
        out = []
        for cell in cells:
            off, w, h, cols, mask = defs[cell]
            index[cell] = dst
            if mask:
                masks[cell] = _mirror_mask(mask, cols or w)
            out.append((off, w, h))
            dst += w * h
        by_stem[stem] = {"extra": sum(w * h for _o, w, h in out), "cells": out}
    if not by_stem:
        return None
    return {"index": index, "mask": masks, "by_stem": by_stem}


def _frames_block(frames_n, flip_n, frames_b, flip_b, bake, ty="u8"):
    """The FRAMES + F_FLIP declarations. Plain when `bake` is None; otherwise a
    module-scope platform fork: SMS/GG get the baked frames (+ the mirror tiles +
    upload_flip() for the boot-upload bake), every other console keeps the FLIP_X
    frames (+ an empty upload_flip()). A residency bake has no tiles here: the
    build appends them to each kind's own sheet."""
    if not bake:
        return [_arr("FRAMES", ty, frames_n), _arr("F_FLIP", "u8", flip_n)]
    if not _has_upload(bake):
        return [
            '    if platform == "sms" or platform == "gamegear" {',
            _arr("FRAMES", ty, frames_b, indent=8),
            _arr("F_FLIP", "u8", flip_b, indent=8),
            "    } else {",
            _arr("FRAMES", ty, frames_n, indent=8),
            _arr("F_FLIP", "u8", flip_n, indent=8),
            "    }",
        ]
    mcount = len(bake["tiles"]) // GB_TILE_BYTES
    return [
        '    if platform == "sms" or platform == "gamegear" {',
        _arr("FRAMES", ty, frames_b, indent=8),
        _arr("F_FLIP", "u8", flip_b, indent=8),
        _arr("MIRROR", "u8", list(bake["tiles"]), indent=8),
        "        function upload_flip() {",
        "            sprite.set_data(%d, %d, MIRROR)" % (bake["base"], mcount),
        "        }",
        "    } else {",
        _arr("FRAMES", ty, frames_n, indent=8),
        _arr("F_FLIP", "u8", flip_n, indent=8),
        "        function upload_flip() {",
        "        }",
        "    }",
    ]


def _has_upload(bake):
    """A boot-upload bake (pixels + upload_flip); a residency bake has none."""
    return bool(bake) and "tiles" in bake


def transpile(clips, cell_tile, module="anim", bake=None):
    """`clips`: {state -> clip dict} (one kind's `[animations.<kind>]`).
    `cell_tile`: {sprite-cell name -> base tile index} (from sheet_sprite_defs).
    `bake` (optional): build_flip_bake(...) for the SMS/GG soft-flip. Returns the
    generated `anim` module source -- the SHELL-driven path (vm.clip): frame(state,
    facing, i) selectors with no kind dimension."""
    frames_n = []
    f_off, f_cnt, f_flip_n, period = _build_kind(clips, cell_tile, frames_n)
    if not frames_n:
        frames_n = [0]

    frames_b, f_flip_b = None, None
    if bake:
        frames_b = []
        _o, _c, f_flip_b, _p = _build_kind(clips, cell_tile, frames_b, mirror=bake["index"],
                                           mirror_mask=bake.get("mask"))
        if not frames_b:
            frames_b = [0]

    lines = [
        "-- anim.mos -- GENERATED by mosaik_anim.py; do not edit by hand.",
        "-- Author the clips in studio.toml [animations.<kind>] and re-run the",
        "-- project's gen script. vm.player reads these selectors (player.set_anim)",
        "-- and drives engine.anim from the native movement state.",
        "",
        'module "%s" {' % module,
    ]
    if _has_upload(bake):
        lines.append('    import "graphics.sprite"')     # only used in the SMS/GG fork
    lines += [
        "    const ST_IDLE: u8 = 0",
        "    const ST_WALK: u8 = 1",
        "    const ST_JUMP: u8 = 2",
        "    const ST_FALL: u8 = 3",
        "",
    ]
    lines += _frames_block(frames_n, f_flip_n, frames_b, f_flip_b, bake)
    lines += [
        _arr("F_OFF", "u8", f_off),
        _arr("F_CNT", "u8", f_cnt),
        _arr("PERIOD", "u8", period),
        "",
        "    -- the i-th frame's base tile for (state, facing)",
        "    function frame(state: u8, facing: u8, i: u8) -> u8 {",
        "        return FRAMES[F_OFF[state * 4 + facing] + i]",
        "    }",
        "    -- frame count for (state, facing); 0 = this state has no clip",
        "    function count(state: u8, facing: u8) -> u8 {",
        "        return F_CNT[state * 4 + facing]",
        "    }",
        "    -- ticks per frame for a state",
        "    function period(state: u8) -> u8 {",
        "        return PERIOD[state]",
        "    }",
        "    -- 1 = mirror this (state, facing) via FLIP_X (a flip_left-derived LEFT)",
        "    function flip(state: u8, facing: u8) -> u8 {",
        "        return F_FLIP[state * 4 + facing]",
        "    }",
        "",
        _export_line(bake),
        "}",
        "",
    ]
    return "\n".join(lines)


def _export_line(bake):
    base = "    export ST_IDLE, ST_WALK, ST_JUMP, ST_FALL, frame, count, period, flip"
    return base + ", upload_flip" if _has_upload(bake) else base


def transpile_kinds(anims, cell_tile, kind_ids, module="clips", bake=None,
                    kind_size=None, cell_mask=None, cell_desc=None,
                    obj16=False):
    """The DATA-DRIVEN runtime path (vm.actor auto-animation): lower a whole world's
    `[animations.<kind>]` to ONE `clips` module keyed by KIND id.

    `anims`: {kind name -> {state -> clip}}. `kind_ids`: {kind name -> id} (world
    [kinds]). `bake` (optional): build_flip_bake(...) for the SMS/GG soft-flip. Emits
    frame(kind, state, facing, i) / count(kind, state, facing) / period(kind, state) /
    flip(kind, state, facing) selectors, so vm.core registers ONE clips module and
    vm.actor animates any placed kind by its id. A kind with no clip has count 0
    (byte-identical to no animation at runtime).

    `kind_size` (optional): {kind name -> (w, h)} metasprite size IN TILES.
    Emitted as `meta_w(kind)`/`meta_h(kind)` selectors ONLY when the world
    actually MIXES sizes -- a uniform world stays byte-identical and keeps
    using the single size the shell passes to `canim.set_clips`. Mixed sizes
    must not collapse to the largest: `sprite.set_meta` fans w*h OAM objects
    per actor, so one 7x6 boss would ask for 42 of the GB's 40 objects and
    every actor on screen renders as garbage.

    `cell_mask` (optional): {sprite-cell name -> per-column BLANK mask}, from
    the sheet manifests' sparse entries. A frame that carries one is drawn with
    `sprite.set_meta_mask`, so its blank columns cost neither a tile nor a
    hardware sprite - the reference engine's metasprites place tiles at authored offsets
    and leave gaps, and a composed rectangle's empty cells stole the eleventh
    letter of a title banner off the GB's 10-sprites-per-scanline limit. Emitted
    only when some frame really is sparse, so every other world stays
    byte-identical.

    `cell_desc` (optional): {frame name -> (pw, ph, [(dy, dx, cell name,
    props), ...])} - the manifests' [[frame]] DESCRIPTOR entries, the reference engine's
    own metasprite model. A clip frame naming one composes the sheet's POOL
    cells (8x16 each) at authored offsets: rows may OVERLAP (its platform
    player's rows are 12 px apart, where a dense rectangle pads to 16 - the
    feet gap) and a cell may REPEAT (a score sprite's 100 frames dedupe to ten
    digit cells). Emits a DESC blob + D_OFF/D_CNT beside FRAMES and a
    `draw()` entry point over `sprite.set_meta_list`; `obj16` says the GB
    family runs 8x16 OBJ mode, which forks the blob per platform (one 8x16
    object per cell there, two stacked 8x8 objects everywhere else). Emitted
    only when some frame really is a descriptor - every other world is
    byte-identical."""
    nk = (max(kind_ids.values()) + 1) if kind_ids else 1
    sizes = {k: tuple(v) for k, v in (kind_size or {}).items()
             if k in kind_ids}
    mixed = len(set(sizes.values())) > 1

    def build(mirror, masks=None, descs=None, pals=None, mirror_mask=None):
        frames = []
        off = [0] * (nk * len(STATES) * NF)
        cnt = [0] * (nk * len(STATES) * NF)
        flip = [0] * (nk * len(STATES) * NF)
        period = [8] * (nk * len(STATES))
        desc_kind = [0] * nk
        for kname, clips in anims.items():
            if kname not in kind_ids:
                raise AnimError("[animations.%s] has no [kinds] id" % kname)
            k = kind_ids[kname]
            n0 = len(frames)
            k_off, k_cnt, k_flip, k_per = _build_kind(
                clips, cell_tile, frames, mirror, cell_mask, masks,
                cell_desc, descs, pals, mirror_mask)
            if descs is not None and any(d is not None for d in descs[n0:]):
                desc_kind[k] = 1
            for j in range(len(STATES) * NF):
                off[k * len(STATES) * NF + j] = k_off[j]
                cnt[k * len(STATES) * NF + j] = k_cnt[j]
                flip[k * len(STATES) * NF + j] = k_flip[j]
            for j in range(len(STATES)):
                period[k * len(STATES) + j] = k_per[j]
        if not frames:
            frames = [0]
        return frames, off, cnt, flip, period, desc_kind

    fmasks = [] if cell_mask else None
    fdescs = [] if cell_desc else None
    fpals = []
    frames_n, off, cnt, flip_n, period, desc_kind = build(
        None, fmasks, fdescs, fpals)
    sparse = bool(fmasks and any(fmasks))
    # A per-frame PALETTE table only exists when some frame asks for a
    # palette (255 = the no-palette sentinel); a world that colours per KIND
    # (or not at all) never emits it and never links vm.canim's palette latch.
    recolour = any(p != 255 for p in fpals)
    has_desc = bool(fdescs and any(d is not None for d in fdescs))
    frames_b = flip_b = fmasks_b = None
    if bake:
        fmasks_b = [] if cell_mask else None
        frames_b, _o, _c, flip_b, _p, _dk = build(
            bake["index"], fmasks_b, None, None, bake.get("mask"))

    lines = [
        "-- clips.mos -- GENERATED by mosaik_anim.py; do not edit by hand.",
        "-- The world's studio.toml [animations.<kind>] clips, keyed by KIND id, for",
        "-- the DATA-DRIVEN runtime: vm.core registers this once + vm.actor animates a",
        "-- placed kind by id (state from movement, facing from direction).",
        "",
        'module "%s" {' % module,
    ]
    if _has_upload(bake) or has_desc:
        lines.append('    import "graphics.sprite"')  # the SMS/GG fork / draw()
    lines += [
        "    const ST_IDLE: u8 = 0",
        "    const ST_WALK: u8 = 1",
        "    const ST_JUMP: u8 = 2",
        "    const ST_FALL: u8 = 3",
        "    const KINDS: u8 = %d" % nk,
        "",
    ]
    lines += _frames_block(frames_n, flip_n, frames_b, flip_b, bake)
    # F_OFF indexes the shared frames list; past 255 frames (an un-trimmed
    # score strip is 100 on its own) it must widen or wrap silently.
    lines += [
        _arr("F_OFF", "u16" if (off and max(off) > 255) else "u8", off),
        _arr("F_CNT", "u8", cnt),
        _arr("PERIOD", "u8", period),
        "",
        "    function frame(kind: u8, state: u8, facing: u8, i: u8) -> u8 {",
        "        return FRAMES[F_OFF[kind * 16 + state * 4 + facing] + i]",
        "    }",
        "    function count(kind: u8, state: u8, facing: u8) -> u8 {",
        "        return F_CNT[kind * 16 + state * 4 + facing]",
        "    }",
        "    function period(kind: u8, state: u8) -> u8 {",
        "        return PERIOD[kind * 4 + state]",
        "    }",
        "    function flip(kind: u8, state: u8, facing: u8) -> u8 {",
        "        return F_FLIP[kind * 16 + state * 4 + facing]",
        "    }",
        "",
    ]
    if sparse:
        # SPARSE frames: a per-column BLANK mask beside each frame's tile. Only
        # emitted when a frame really has gaps, so a world of solid rectangles
        # is byte-identical and never links the masked draw path.
        lines += [
            "    -- Per-frame BLANK-COLUMN mask (bit c = column c of this frame",
            "    -- draws nothing). The frame's sheet holds only its DRAWN",
            "    -- columns, so a blank one costs neither a tile nor one of the",
            "    -- GB's 10 sprites per scanline.",
        ]
        if fmasks_b is not None and fmasks_b != fmasks:
            # a sparse cell's MIRROR blanks the mirrored columns (SMS/GG)
            lines += ['    if platform == "sms" or platform == "gamegear" {',
                      _arr("F_MSK", "u16", fmasks_b, indent=8),
                      "    } else {",
                      _arr("F_MSK", "u16", fmasks, indent=8),
                      "    }"]
        else:
            lines += [_arr("F_MSK", "u16", fmasks)]
        lines += [
            "",
            "    function frame_mask(kind: u8, state: u8, facing: u8, i: u8) -> u16 {",
            "        return F_MSK[F_OFF[kind * 16 + state * 4 + facing] + i]",
            "    }",
            "",
        ]
    if recolour:
        # PER-FRAME SPRITE PALETTE (the reference engine's per-metasprite-tile palette
        # read at the FRAME level): bits 0-2 are the CGB OBJ palette and bit 4
        # is the DMG OBP0/OBP1 select, which is exactly how its own compiler
        # spells a metasprite's props. That covers all three shapes its editor
        # authors - a whole sprite recoloured per STATE (an exploding mine
        # turns orange), per ANIMATION, and a two-frame FLASH on the DMG (the
        # same art through OBP0 then OBP1). Per-CELL colour is a different
        # mechanism ([kind_tile_palettes], applied once at room load) and is
        # untouched by this table.
        lines += [
            "    -- Per-frame SPRITE PALETTE: bits 0-2 the CGB OBJ palette,",
            "    -- bit 4 (0x10) the DMG OBP0/OBP1 select; 255 = this frame",
            "    -- authors NO palette, so the animator must not touch the",
            "    -- one the room load applied (the per-cell map). vm.canim",
            "    -- writes it when the drawn frame changes and not otherwise.",
            _arr("F_PAL", "u8", fpals),
            "",
            "    function frame_pal(kind: u8, state: u8, facing: u8, i: u8) -> u8 {",
            "        return F_PAL[F_OFF[kind * 16 + state * 4 + facing] + i]",
            "    }",
            "",
        ]
    if mixed or has_desc:
        # Per-kind metasprite size (see the docstring): a kind with no entry
        # falls back to 1x1, which draws one tile rather than reading past the
        # sheet. (Descriptor worlds always emit it - draw()'s flip mirrors
        # around META_W*8 and fan() falls back to it for dense kinds.)
        mw = [1] * nk
        mh = [1] * nk
        for kname, (w, h) in sizes.items():
            mw[kind_ids[kname]] = max(1, int(w))
            mh[kind_ids[kname]] = max(1, int(h))
        lines += [
            "    -- Per-kind metasprite size in TILES. This world mixes sprite",
            "    -- shapes, so every actor must be drawn at ITS OWN size:",
            "    -- set_meta fans w*h OAM objects, and the GB has only 40.",
            _arr("META_W", "u8", mw),
            _arr("META_H", "u8", mh),
            "",
            "    function meta_w(kind: u8) -> u8 {",
            "        return META_W[kind]",
            "    }",
            "    function meta_h(kind: u8) -> u8 {",
            "        return META_H[kind]",
            "    }",
            "",
        ]
    if has_desc:
        # DESCRIPTOR frames (the reference engine's metasprite model). The blob is
        # per-OBJECT entries (dy, dx, dtile, props); under obj_8x16 a console
        # WITH the mode (platforms.OBJ16_CONSOLES) draws one 8x16 object per
        # pool cell while every 8x8-OBJ console draws the cell as two stacked
        # objects, so the DATA forks per platform and the walker
        # (sprite.set_meta_list) stays mode-agnostic.
        def _blob(mode16):
            blob, d_off, d_cnt = [], [], []
            seen = {}       # identical frames share ONE run: a non-directional
            #                 clip repeats each frame once per FACING, which
            #                 quadrupled the blob before this dedupe
            for d in fdescs:
                if d is None:
                    d_off.append(0)
                    d_cnt.append(0)
                    continue
                _pw, _ph, objs = d
                ents = []
                for (dy, dx, cn, pr) in objs:
                    if cn not in cell_tile:
                        raise AnimError("[[frame]] descriptor references "
                                        "unknown pool cell '%s'" % cn)
                    t = cell_tile[cn]
                    if mode16:
                        ents.append((dy, dx, t, pr))
                    else:
                        ents.append((dy, dx, t, pr))
                        ents.append((dy + 8, dx, t + 1, pr))
                key = tuple(ents)
                if key in seen:
                    off = seen[key]
                else:
                    off = seen[key] = len(blob)
                    for (dy, dx, t, pr) in ents:
                        blob += [dy & 0xFF, dx & 0xFF, t & 0xFF, pr & 0xFF]
                d_off.append(off)
                d_cnt.append(len(ents))
            return blob, d_off, d_cnt

        def _desc_arrs(mode16, indent=4):
            blob, d_off, d_cnt = _blob(mode16)
            return [_arr("DESC", "u8", blob or [0], indent),
                    _arr("D_OFF", "u16", d_off, indent),
                    _arr("D_CNT", "u8", d_cnt, indent)]

        lines += ["    -- Per-frame OBJECT descriptors (the reference engine's metasprite",
                  "    -- model, sprite.set_meta_list): rows may OVERLAP at their",
                  "    -- authored offsets and a pool cell may REPEAT - the feet",
                  "    -- gap and the tile dedupe a dense rectangle cannot say."]
        if obj16:
            from mosaik.platforms import obj16_guard
            lines += [obj16_guard("    ")]
            lines += _desc_arrs(True, 8)
            lines += ["    } else {"]
            lines += _desc_arrs(False, 8)
            lines += ["    }"]
        else:
            lines += _desc_arrs(False)
        # D_KIND: which kinds draw through the descriptor; D_FAN: the OAM cost
        # in 8x8-OBJECT units (a pool cell is two), so the generated rooms'
        # existing obj16 halving yields the GB object count and the 8x8
        # consoles use it whole - the same unit dense w*h has.
        d_fan = [0] * nk
        for kname, clips_d in anims.items():
            k = kind_ids.get(kname)
            if k is None or not desc_kind[k]:
                continue
            mx = 0
            for st in STATES:
                clip = clips_d.get(st)
                if not clip:
                    continue
                for fac in FACINGS:
                    for cell in _resolve(clip)[fac][0]:
                        d = (cell_desc or {}).get(cell)
                        if d:
                            mx = max(mx, 2 * len(d[2]))
            d_fan[k] = mx
        lines += [
            _arr("D_KIND", "u8", desc_kind),
            _arr("D_FAN", "u8", d_fan),
            "",
            "    function is_desc(kind: u8) -> u8 {",
            "        return D_KIND[kind]",
            "    }",
            "    -- the OAM fan cost of one actor of `kind`, in 8x8-object units",
            "    function fan(kind: u8) -> u8 {",
            "        if D_KIND[kind] == 1 {",
            "            return D_FAN[kind]",
            "        }",
            "        return META_W[kind] * META_H[kind]",
            "    }",
            "    -- draw frame i of (kind, state, facing) at OAM `base`, tiles",
            "    -- offset by `tile` (the kind's VRAM base under residency)",
            "    function draw(base: u8, kind: u8, state: u8, facing: u8, i: u8, tile: u8) {",
            "        var fi: u16 = F_OFF[kind * 16 + state * 4 + facing] + i",
            "        sprite.set_meta_list(base, META_W[kind] * 8, tile, DESC, D_OFF[fi], D_CNT[fi])",
            "    }",
            "",
        ]
    # The BATCHED selector for vm.canim.apply: the drawn frame's tile, its
    # FLIP_X (bit 8) and - when the world has descriptor frames - whether the
    # kind draws through the descriptor (bit 9), in ONE cross-bank call.
    # Three separate banked reads at every animator STEP frame were most of
    # room 10's 2->3 LCD crossing.
    # Emitted for every clips module; the runtime arm keys on its presence
    # (VM_CLIP_SEL), so a project rebuilt against an OLD clips.mos is
    # byte-identical.
    lines += [
        "    -- Batched per-frame selector for vm.canim.apply: tile | FLIP_X",
        "    -- (bit 8)%s%s -"
        % (" | is_desc (bit 9)" if has_desc else "",
           " | palette+1 (bits 10..15, 0 = frame authors none)"
           if recolour else ""),
        "    -- one cross-bank call where the animator step made several.",
        "    function sel(kind: u8, state: u8, facing: u8, i: u8) -> u16 {",
        "        var j: u16 = kind * 16 + state * 4 + facing",
        "        var v: u16 = FRAMES[F_OFF[j] + i]",
        "        if F_FLIP[j] == 1 {",
        "            v |= 256",
        "        }",
    ]
    if has_desc:
        lines += [
            "        if D_KIND[kind] == 1 {",
            "            v |= 512",
            "        }",
        ]
    if recolour:
        # The frame PALETTE rides bits 10..15 as value+1 (255, the
        # no-palette sentinel, encodes as 0): its top authored value is
        # CGB bits 0-2 | DMG OBP bit 4 = 23, so +1 fits the six bits.
        # Every animator past the fast path used to make its own banked
        # frame_pal call on a step frame - nine at once in room 10.
        lines += [
            "        var p: u16 = F_PAL[F_OFF[j] + i]",
            "        if p != 255 {",
            "            v |= (p + 1) << 10",
            "        }",
        ]
    lines += [
        "        return v",
        "    }",
        "",
    ]
    lines += [
        ("    export ST_IDLE, ST_WALK, ST_JUMP, ST_FALL, KINDS, frame, count, "
         "period, flip, sel" + (", frame_mask" if sparse else "")
         + (", frame_pal" if recolour else "")
         + (", meta_w, meta_h" if (mixed or has_desc) else "")
         + (", is_desc, fan, draw" if has_desc else "")
         + (", upload_flip" if _has_upload(bake) else "")),
        "}",
        "",
    ]
    return "\n".join(lines)
