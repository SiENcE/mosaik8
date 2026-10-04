"""mosaik_scenes.filtering - per-console content filtering (platforms tags -> per-target buckets)."""


try:  # canonicalise a hand-authored short id ("gb" -> "gameboy"); optional.
    from mosaik.platforms import canonical_platform as _canon_platform
except Exception:  # pragma: no cover - engine layout drift / bare use
    def _canon_platform(name):
        return str(name)


def _entity_platforms(entity):
    """The per-console CONTENT FILTER on a placed object / door / trigger dict: an
    allow-list of canonical target ids the entity is included on (empty = every
    target). Reads the authoring-only `platforms` key (canonicalised), ignoring a
    non-list or empty value so an untagged entity is byte-identical."""
    raw = entity.get("platforms")
    if not raw or not isinstance(raw, (list, tuple)):
        return ()
    return tuple(_canon_platform(p) for p in raw)


def _platform_buckets(tag_lists):
    """Group targets into equivalence buckets by which entities they KEEP.

    `tag_lists` is a list of parallel per-family tag lists (each a list of the
    per-entity `platforms` tuples, e.g. [obj_plat, door_plat, trig_plat]). Returns
    `None` when nothing is tagged (the caller emits a single guard-free block =
    byte-identical); otherwise a list of `(platform_ids_or_None, keep_lists)`, where
    `keep_lists` mirrors `tag_lists` giving the KEPT index list per family for that
    bucket, ordered with the untagged-default bucket (`platform_ids is None`) LAST so
    it emits as the `else`. Needs no target universe: M = the union of mentioned ids;
    unmentioned targets all share the untagged-only default (the `else`)."""
    mentioned = sorted({p for tags in tag_lists for tup in tags for p in tup})
    if not mentioned:
        return None

    def keeps(target):
        # For a given target, keep entities that are untagged OR list this target.
        return tuple(tuple(i for i, tup in enumerate(tags)
                           if not tup or target in tup)
                     for tags in tag_lists)

    default = tuple(tuple(i for i, tup in enumerate(tags) if not tup)
                    for tags in tag_lists)

    groups = {}  # keep-key -> [target ids], insertion-ordered
    for t in mentioned:
        k = keeps(t)
        if k == default:
            continue  # folded into the `else` (its content == the default view)
        groups.setdefault(k, []).append(t)

    buckets = [(ids, list(k)) for k, ids in groups.items()]
    buckets.append((None, list(default)))  # the else, always emitted, always last
    return buckets


def _clear_plat(d):
    """A shallow copy of an entity/scene dict with its `platforms` key removed (so a
    per-bucket sub-transpile does no further, redundant forking)."""
    d = dict(d)
    d.pop("platforms", None)
    return d


def _stub_scene(sc, has_collision):
    """The minimal stand-in for a scene DROPPED on a console (Stage 2): keeps the NAME
    (so its <NAME>_MAP symbol stays defined + exported) and its scene INDEX (so doors
    resolve + nothing renumbers), but shrinks the map to 1x1 (dropping the room's tile
    bytes) and empties its objects. A 1x1 collision cell is kept iff the world uses
    collision, so `has_collision` stays uniform across the forked branches."""
    stub = {"name": sc.get("name"), "map": [0], "map_w": 1, "map_h": 1, "object": []}
    if has_collision:
        stub["collision"] = [0]
    return stub


def _split_marked(text):
    """Split a `transpile(..., _mark=True)` output into (head, body, tail) line lists
    around the two `_SCENE_FORK_MARK` sentinels: head = module open + imports, body =
    the forkable declarations, tail = the (uniform) export + close."""
    lines = text.rstrip("\n").split("\n")
    a = lines.index(_SCENE_FORK_MARK)
    b = lines.index(_SCENE_FORK_MARK, a + 1)
    return lines[:a], lines[a + 1:b], lines[b + 1:]


_SCENE_FORK_MARK = "    -- <<SCENE_FORK_BODY>>"
