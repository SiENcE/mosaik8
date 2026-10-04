"""mosaik_scenes.palette_fold - the world colour model on consoles with FOUR
palette slots a layer (and the project default slot rows).

The colour tier addresses 8 hardware background and 8 sprite palette slots per
scene (the GBC's own shape). Four consoles have four:

- SMS / Game Gear: 16 CRAM entries a layer, a tile's slot picks which four its
  2bpp pixels expand onto (`gbs_bkg_data_pal`);
- NES: four background and four sprite palettes (`gbs_bkg_attrs`);
- PC Engine: four VCE background palettes the attribute upload selects.

All four MASK the slot with `& 3`, so without a fold a tile on slot 5 silently
shows slot 1's colours there. The reference-engine importer folds its own worlds while
it still deduplicates tiles (a tile that differs only by a merged slot becomes
one tile, which is what keeps the carried SMS conversion under that console's
tile cap), so a conversion arrives already folded and this is a no-op on it.
A hand-authored world is folded here, at transpile time, for any project that
targets one of the four.

The rule, the same for backgrounds (per scene) and sprites (per world, because
a kind wears one slot in every room):

1. slot 0 stays slot 0 - the dialogue window, the glyph band and a tile with no
   slot all draw through background palette 0 on every console;
2. a USED slot 1..3 keeps its number, so what an author sees on slots 0..3 is
   the same on every console;
3. used slots 4..7 take the FREE positions among 1..3, most used first (ties to
   the lower slot);
4. whatever is left merges onto the kept slot whose PALETTE is nearest in RGB
   (ties to the lower slot);
5. an unused slot parks on 0.

Slots that name the same library palette are one slot for the count (merging
them loses nothing). Usage counts TILES of the scene's tileset per slot for the
background, and kinds + metasprite cells for sprites.

Pure data in, data out: no emission here. The scene transpiler emits the folded
tables behind `if platform` forks and the studio's per-console report reads the
same answers, so the preview and the ROM cannot disagree.
"""

FOLD_CONSOLES = ("sms", "gamegear", "nes", "pce")
FOLD_SLOTS = 4
PAL_SLOTS = 8


def _parse_rgb(color):
    if isinstance(color, str):
        s = color.strip().lstrip("#")
        try:
            return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return (0, 0, 0)
    try:
        return tuple(int(v) & 0xFF for v in list(color)[:3])
    except (TypeError, ValueError):
        return (0, 0, 0)


def _slot_list(v):
    return None if v is None else [int(x) for x in v]


def scene_palette_rows(world):
    """``(bkg rows, spr rows)``, one entry per scene: the scene's own
    ``bkg_palettes`` / ``spr_palettes``, else the world's ``[world]
    default_bkg_palettes`` / ``default_spr_palettes``, else None.

    THE one reading of those keys: the scene transpiler's colour gate and the
    rooms generator's must agree, or a room loads tables nobody emitted."""
    w = world.get("world", {}) or {}
    dbkg = _slot_list(w.get("default_bkg_palettes"))
    dspr = _slot_list(w.get("default_spr_palettes"))
    bkg, spr = [], []
    for sc in world.get("scene", []) or []:
        b = sc.get("bkg_palettes")
        s = sc.get("spr_palettes")
        bkg.append(_slot_list(b) if b is not None else dbkg)
        spr.append(_slot_list(s) if s is not None else dspr)
    return bkg, spr


def _sel(row, slot):
    row = list(row or [])
    return int(row[slot]) if slot < len(row) and row[slot] is not None else 0


def _dist(lib, a, b):
    ca = lib[a] if 0 <= a < len(lib) else [(0, 0, 0)] * 4
    cb = lib[b] if 0 <= b < len(lib) else [(0, 0, 0)] * 4
    return sum((x[0] - y[0]) ** 2 + (x[1] - y[1]) ** 2 + (x[2] - y[2]) ** 2
               for x, y in zip(ca, cb))


def fold_slots(usage, sel, lib):
    """One layer's 8 slots -> ``(remap, kept)``.

    ``usage``: slot -> count (only slots with count > 0 are used); ``sel``:
    slot -> library index (an 8-long row); ``lib``: library index -> 4 RGB.
    ``remap[s]`` is slot s's folded slot 0..3; ``kept[i]`` is the ORIGINAL slot
    folded slot i shows (None when nothing lands there)."""
    sel = [_sel(sel, s) for s in range(PAL_SLOTS)]
    # Slots naming the same palette are one slot: count them on the lowest.
    canon = {s: min(t for t in range(PAL_SLOTS) if sel[t] == sel[s])
             for s in range(PAL_SLOTS)}
    used = {}
    for s, n in usage.items():
        if n and 0 <= s < PAL_SLOTS:
            used[canon[s]] = used.get(canon[s], 0) + n
    kept = [None] * FOLD_SLOTS
    kept[0] = 0
    remap = {0: 0}
    for s in (1, 2, 3):
        if s in used:
            kept[s] = s
            remap[s] = s
    extra = sorted((s for s in used if s >= FOLD_SLOTS),
                   key=lambda s: (-used[s], s))
    free = [i for i in (1, 2, 3) if kept[i] is None]
    for s in extra:
        if free:
            i = free.pop(0)
            kept[i] = s
            remap[s] = i
        else:
            near = min((i for i in range(FOLD_SLOTS) if kept[i] is not None),
                       key=lambda i: (_dist(lib, sel[s], sel[kept[i]]), i))
            remap[s] = near
    out = []
    for s in range(PAL_SLOTS):
        c = canon[s]
        out.append(remap.get(c, 0) if c in used or c == 0 else 0)
    return out, kept


def folded_row(sel, kept):
    """A scene's 8 library indices in FOLDED slot order: folded slot i holds
    the palette of original slot ``kept[i]``; the rest repeat folded slot 0."""
    base = _sel(sel, 0)
    row = [(_sel(sel, k) if k is not None else base) for k in kept]
    return row + [row[0]] * (PAL_SLOTS - FOLD_SLOTS)


def library_rgb(world):
    return [[_parse_rgb(c) for c in (p.get("colors") or [])][:4]
            for p in world.get("palette", []) or []]


def bkg_fold(world, tile_rows=None):
    """Per scene ``(remap8, folded8)`` for the background, or None when no
    scene puts a tile on a slot >= 4 (nothing to fold).

    ``tile_rows`` overrides each scene's ``tile_palette`` (the transpiler pads
    them to the scene's tile count)."""
    scenes = world.get("scene", []) or []
    rows = tile_rows if tile_rows is not None else \
        [sc.get("tile_palette") for sc in scenes]
    if not any(int(t) & 7 >= FOLD_SLOTS for r in rows if r for t in r):
        return None
    lib = library_rgb(world)
    bkg, _spr = scene_palette_rows(world)
    out = []
    for i, _sc in enumerate(scenes):
        usage = {}
        for t in rows[i] or []:
            s = int(t) & 7
            usage[s] = usage.get(s, 0) + 1
        remap, kept = fold_slots(usage, bkg[i], lib)
        out.append((remap, folded_row(bkg[i], kept)))
    return out


def spr_fold(world):
    """``(remap8, [folded8 per scene])`` for sprites, or None when no kind (or
    metasprite cell) is on a slot >= 4."""
    kp = world.get("kind_palettes") or {}
    ktp = world.get("kind_tile_palettes") or {}
    slots = [int(v) & 7 for v in kp.values()]
    for cells in ktp.values():
        slots += [int(c) & 7 for c in cells or []]
    if not any(s >= FOLD_SLOTS for s in slots):
        return None
    usage = {}
    for s in slots:
        usage[s] = usage.get(s, 0) + 1
    lib = library_rgb(world)
    _bkg, spr = scene_palette_rows(world)
    # The distance is judged on the first scene that loads sprite palettes (a
    # studio world writes the same row into every scene).
    ref = next((r for r in spr if r is not None), None)
    remap, kept = fold_slots(usage, ref, lib)
    return remap, [folded_row(r, kept) if r is not None else None for r in spr]


def project_targets(base_dir):
    """`mosaik.toml [project] target_platforms` of the project owning a world at
    ``base_dir`` (lower-cased), or [] for a bare world. A world sits at the
    project root, in `assets/`, or split one level deeper."""
    import os
    d = os.path.abspath(base_dir or ".")
    for _ in range(3):
        mp = os.path.join(d, "mosaik.toml")
        if os.path.isfile(mp):
            try:
                from .loaders import _load_toml
                proj = _load_toml(mp).get("project", {}) or {}
            except Exception:  # noqa: BLE001
                return []
            return [str(p).strip().lower()
                    for p in (proj.get("target_platforms") or [])]
        d = os.path.dirname(d)
    return []


def targets_fold(base_dir):
    """Whether the project builds for a 4-slot console at all."""
    return bool(set(FOLD_CONSOLES) & set(project_targets(base_dir)))
