"""mosaik_vm.hud - src/hud.mos generation (the studio HUD editor's overlay panels).

Reads ``scripts/hud.toml`` (the studio ``VmHud`` library) + the compiled heap
variable map and emits ``src/hud.mos``: a ``show(id)`` that draws a panel's labels
+ var fields onto the GB WINDOW band (``text.to_window``), an ``update()`` that
refreshes a var field only when its heap cell changed (a cached last-value diff,
so a persistent HUD is cheap and there is no per-var-change redraw script for the
author to remember - the reference-engine overlay-HUD pain point), and a ``hide()``.

Regenerated from project content like ``songs.mos`` / ``rooms.mos``; a project
with no ``hud.toml`` carries no module (byte-identical). Wired by a game that
calls ``core.set_hud(hud.update)`` + ``hud.show(id)`` (Stage A; the generated
``rooms.mos`` scene binding is a later increment).

INCREMENT 1 scope: the GB-family WINDOW band -
``label`` + ``var`` elements, ``bottom`` anchor. Other anchors/backends
(name-table rows, sprites, the Lynx present hook), gauges/icons/tiles and panel
frame styles are later stages.
"""
import os

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None

from .fmt import _escape
from .loader import compile_path
from .isa import VmError
from .projio import _find_world


def load_hud_panels(scripts_dir):
    """The panels authored in ``scripts/hud.toml`` (a list of dicts), or [] when
    there is no HUD. Mirrors the studio ``VmHud`` on-disk shape."""
    if toml is None:
        return []
    path = os.path.join(scripts_dir, "hud.toml")
    if not os.path.isfile(path):
        return []
    try:
        data = toml.load(path)
    except Exception:
        return []
    return [p for p in (data.get("panel", []) or []) if isinstance(p, dict) and p.get("name")]


def _dyn_fields(panel, variables):
    """The DYNAMIC elements of a panel (var + gauge) whose heap cell resolves -- the
    ones update() diffs + redraws. A var/gauge whose cell is not in `variables` (no
    script references it yet) is skipped, so the module always compiles."""
    out = []
    for e in panel.get("element", []):
        if e.get("kind") in ("var", "gauge") and e.get("var") in variables:
            if e.get("kind") == "gauge" and not e.get("max"):
                continue                    # a gauge needs a const `max` (its cell count)
            out.append(e)
    return out


def _slot(e, key, script_names):
    """The event script an element's On Show / On Change slot binds, if it exists
    in the project (else None -- a dangling ref is skipped)."""
    name = e.get(key)
    return name if (name and name in script_names) else None


def _icons(panel):
    """The icon elements of a panel (pinned OAM sprites)."""
    return [e for e in panel.get("element", []) if e.get("kind") == "icon"]


# HUD icons are pinned OAM sprites. Reserve slots from the TOP of OAM (39 down) so
# they never collide with the actor pool / player, which occupy the LOW slots
# (vm.actor draws actor i to slot i; the player's base sits just above the pool).
HUD_ICON_TOP = 39

# ---------------------------------------------------------------------------
# The SPRITE-TEXT backend (a HUD band over a SCROLLING room).
#
# A HUD is an overlay: it must hold a fixed SCREEN position while the level
# moves under it. Each console answers that differently, and only two of them
# answer it for free:
#
#   * GB family - the hardware WINDOW layer (`text.to_window`). Never scrolls.
#   * Lynx      - no tilemap at all; `hud.draw()` runs inside the present hook,
#                 so the whole band is re-composited into every frame.
#   * SMS/GG/PCE - a persistent tilemap and NO window. Text plotted there is a
#                 MAP cell, so it scrolls away with the level.
#
# Re-plotting the band each frame in screen space (what the dialogue box does)
# is not an answer for a PERSISTENT HUD: a cell cannot express a sub-tile
# scroll, so the band would sawtooth by up to 7 px forever, and the box path
# only gets away with it because it SNAPS the scroll and HOLDS the camera while
# it is up. Nor is a raster split: the SMS VDP latches its vertical scroll once
# per frame, so a mid-frame vscroll change is impossible there.
#
# So on those three consoles a HUD over a scrolling room is drawn with SPRITES,
# which live in screen space by construction (the same answer the gauge already
# gives on the Lynx/PCE). Labels and var fields get their glyphs uploaded into
# sprite tiles at run time by `sprite.font_glyph`, out of the console font the
# toolchain LINKS (GBDK's font_ibm on SMS/GG, cc65's pce_font on the PCE), so
# the generated module carries character codes and never a font's pixels.
#
# It is resolved PER CONSOLE from the world's own room sizes: a world whose
# rooms all fit a console's screen never scrolls there and keeps the (cheaper,
# tile-free) text path, byte-identical. A project with no world.toml to measure
# keeps it too.
_SPRITE_TEXT_CONSOLES = ("sms", "gamegear", "pce")

# The VISIBLE screen of each of those, in pixels. The Game Gear shares the SMS
# VDP but shows a 160x144 crop of it, so a room the SMS displays whole still
# scrolls there - which is why this is a per-console table and not one number.
_SCREEN_PX = {"sms": (256, 192), "gamegear": (160, 144), "pce": (256, 192)}

# Where the uploaded glyph tiles go, per console, counted DOWN from the top of the
# sprite tile table so they never collide with the game's own sheet (which
# uploads from 0) - the rule the emote reservation follows. SMS/GG sprite
# patterns are a separate 256-tile VDP area (0x2000-0x3FFF since 2026-10-24,
# rooms/context.py) whose top four are the emote's, so the glyphs sit just
# below those; the PCE's table is the cc65 backend's
# 40-entry RAM table (CC65_MAX_TILES), which a big sheet can crowd - an
# out-of-range upload is dropped by the engine, so the glyphs go missing rather
# than corrupting art.
_GLYPH_TILE_TOP = {"sms": 252, "gamegear": 252, "pce": 40}


def _world_max_px(root):
    """The biggest room in this project, in PIXELS, or None when there is no
    world.toml to measure (a hand-wired project - keep the text path)."""
    if toml is None or not root:
        return None
    # The ONE world lookup (root, assets/, split dirs): a scaffolded project
    # keeps its world in assets/world.toml, which a root-only probe missed, so
    # its HUD plotted into the scrolling map on SMS / GG / PCE.
    path = _find_world(root)
    if path and os.path.isdir(path):
        path = os.path.join(path, "world.toml")
    if not path:
        return None
    try:
        data = toml.load(path)
    except Exception:
        return None
    world = data.get("world", {}) or {}
    dw, dh = int(world.get("map_w", 0) or 0), int(world.get("map_h", 0) or 0)
    mw = mh = 0
    for s in (data.get("scene", []) or []):
        if not isinstance(s, dict):
            continue
        rows = s.get("map") or []
        w = int(s.get("map_w", 0) or 0) or dw or (len(rows[0]) if rows else 0)
        h = int(s.get("map_h", 0) or 0) or dh or len(rows)
        mw, mh = max(mw, w), max(mh, h)
    if not (mw and mh):
        return None
    return mw * 8, mh * 8


def sprite_text_consoles(root):
    """The consoles whose HUD band must be drawn with SPRITES for this project:
    the window-less tilemap consoles (SMS / Game Gear / PCE) on which some room
    is bigger than the screen, so the background scrolls and a plotted band
    would scroll with it. Empty for a world that fits everywhere (the tile path
    stays, byte-identical)."""
    size = _world_max_px(root)
    if not size:
        return ()
    w, h = size
    return tuple(c for c in _SPRITE_TEXT_CONSOLES
                 if w > _SCREEN_PX[c][0] or h > _SCREEN_PX[c][1])


def _panel_glyphs(panels, variables, consoles=None):
    """The distinct characters the sprite-text backend must upload, sorted (so the
    ten digits stay CONTIGUOUS, which is what lets a var field index them).
    Labels contribute their own characters; any var field contributes 0-9.
    With `consoles`, an element whose Consoles list names none of them (drawn
    elsewhere only) contributes nothing."""
    chars = set()
    for p in panels:
        for e in p.get("element", []):
            cs = _element_consoles(p, e)
            if consoles is not None and cs and not set(cs) & set(consoles):
                continue
            if e.get("kind") == "label" and e.get("text"):
                chars |= {c for c in str(e["text"]) if c != " "}
            elif e.get("kind") == "var" and e.get("var") in variables:
                chars |= set("0123456789")
    return sorted(chars)


def _sprite_label(base, gindex, text, x, row, ind):
    """Draw a fixed label with one sprite per non-space character."""
    out, slot = [], base
    for i, ch in enumerate(str(text)):
        if ch == " ":
            continue                     # a space needs no sprite (and no glyph)
        out += [ind + "    sprite.set_tile(%d, HUD_GLYPH_BASE + %d)" % (slot, gindex[ch]),
                ind + "    sprite.move(%d, %d, (%s) * 8)   -- '%s'"
                % (slot, (x + i) * 8, row, ch)]
        slot += 1
    return out


def _sprite_number(base, gindex, width, zero_pad, x, row, ind):
    """Draw a var field's value right-aligned across `width` cells, one sprite per
    cell, out of the uploaded digit glyphs. A leading cell shows '0' when the field
    pads with zeros, else its sprite is parked (a blank needs no glyph). The digit
    is picked by the cell's own significance, so nothing is carried between cells
    and a shrinking number leaves no stale digit behind."""
    zero = gindex["0"]
    out = ["%s    var nd: u8 = 1" % ind,
           "%s    if v >= 10 { nd = 2 }" % ind,
           "%s    if v >= 100 { nd = 3 }" % ind,
           "%s    var off: u8 = 0" % ind,
           "%s    if nd < %d { off = %d - nd }" % (ind, width, width)]
    for j in range(width):
        slot = base + j
        p = 10 ** (width - 1 - j)
        digit = "v % 10" if p == 1 else "v / %d %% 10" % p
        out += [ind + "    if off > %d {" % j]
        if zero_pad:
            out += [ind + "        sprite.set_tile(%d, HUD_GLYPH_BASE + %d)" % (slot, zero),
                    ind + "        sprite.move(%d, %d, (%s) * 8)" % (slot, (x + j) * 8, row)]
        else:
            out += [ind + "        sprite.move(%d, 0, SCREEN_HEIGHT)" % slot]
        out += [ind + "    } else {",
                ind + "        var d%d: u8 = %s" % (j, digit),
                ind + "        sprite.set_tile(%d, HUD_GLYPH_BASE + d%d)" % (slot, j),
                ind + "        sprite.move(%d, %d, (%s) * 8)" % (slot, (x + j) * 8, row),
                ind + "    }"]
    return out


# ---------------------------------------------------------------------------
# PER-CONSOLE ELEMENTS and the panel's own SPRITE SHEET (2026-10-24).
#
# An element's `consoles` list (empty = every console) says where it is drawn,
# so one panel can carry a full status strip for the window-layer consoles and a
# compact sprite one for the SMS / Game Gear, whose hardware shows 8 sprites a
# scanline and has no window to pin a band to.
#
# A panel may name a SPRITE SHEET (`sheet` = a sheet stem in [assets] sprites):
# its `icon`, `var` and `gauge` elements that name a CELL of it (`sprite`) are
# drawn with that sheet's art instead of the console font or bkg tiles - an icon
# is its cell, a var's digits are the cells `<prefix>0..9`, a gauge is a bar of
# `width` sprites over the cells `<prefix>0..N` (N + 1 fill states each). Placed
# in PIXELS (`px` / `py`, else the cell grid). The panel's show uploads the
# sheet into tiles reserved at the top of the sprite area (below the glyph band)
# in the panel's SMS/GG sprite palette slot (`sheet_slot`), and its sprites take
# OAM slots from the top of the table down; the generated room load keeps the
# actors and their art below both (`smsgg_reservation`). SMS / Game Gear only
# for now (`SHEET_CONSOLES`): their 256-tile sprite area has the room.
SHEET_CONSOLES = ("sms", "gamegear")
SHEET_OAM_TOP = 63                  # the SMS/GG sprite table's last slot (64 of them)
SPRITES_PER_LINE = 8                # what the SMS/GG VDP draws on one scanline


def _console_guard(consoles):
    """`platform == "a" or ...` for a consoles list, or None for every console."""
    return " or ".join('platform == "%s"' % c for c in consoles) if consoles else None


def _element_consoles(panel, e):
    """The consoles an element is drawn on: its own list, narrowed by its panel's."""
    own = [str(c) for c in (e.get("consoles") or [])]
    pan = [str(c) for c in (panel.get("consoles") or [])]
    if own and pan:
        return [c for c in own if c in pan] or ["__none__"]
    return own or pan


def _is_sheet_element(panel, e):
    return bool(panel.get("sheet") and e.get("sprite")
                and e.get("kind") in ("icon", "var", "gauge"))


def load_sheet(root, stem):
    """{"cells": {name: (tile_offset, w_tiles, h_tiles)}, "order": [names],
    "tiles": total} for `assets/<stem>.png` (+ its .sprites.toml), or None."""
    if not (root and stem):
        return None
    png = os.path.join(root, "assets", str(stem) + ".png")
    if not os.path.isfile(png):
        return None
    try:
        from mosaik_assets import sheet_sprite_defs
        defs = sheet_sprite_defs(png)
    except Exception:
        return None
    if not defs:
        return None
    cells = {n: (off, w, h) for n, off, w, h in defs}
    n, off, w, h = defs[-1]
    return {"cells": cells, "order": [d[0] for d in defs], "tiles": off + w * h}


def _cell_series(sheet, name):
    """The cells `<prefix>0`, `<prefix>1`, ... starting at `name` (which ends in 0):
    their tile offsets, in order. A single named cell (an icon) is [its offset]."""
    cells = sheet["cells"]
    if name not in cells:
        return []
    if not name.endswith("0"):
        return [cells[name][0]]
    prefix, out, i = name[:-1], [], 0
    while "%s%d" % (prefix, i) in cells:
        out.append(cells["%s%d" % (prefix, i)][0])
        i += 1
    return out


def _sheet_slots_of(e):
    """Hardware sprites a sheet element draws: an icon 1, a var its width, a bar its width."""
    if e.get("kind") == "icon":
        return 1
    return max(1, int(e.get("width", 0) or 1))


def sheet_line_load(panels, sheets, console):
    """(sprites, line) of the busiest scanline the sheet-drawn elements of any one
    panel put on `console`, or (0, None). The SMS/GG VDP draws 8 sprites a line
    and drops the rest; the HUD's slots are the table's last, so it is the HUD
    that drops."""
    worst = (0, None)
    for p in panels:
        sheet = (sheets or {}).get(p.get("sheet"))
        if not sheet:
            continue
        count = {}
        rows = int(p.get("rows", 1))
        for e in p.get("element", []):
            if not _is_sheet_element(p, e):
                continue
            cs = _element_consoles(p, e)
            if cs and console not in cs:
                continue
            cell = sheet["cells"].get(e.get("sprite"))
            h = (cell[2] if cell else 1) * 8
            py = int(e["py"]) if e.get("py") is not None else (18 - rows + int(e.get("y", 0))) * 8
            for line in range(py, py + h):
                count[line] = count.get(line, 0) + _sheet_slots_of(e)
        for line, n in count.items():
            if n > worst[0] or (n == worst[0] and worst[1] is not None and line < worst[1]):
                worst = (n, line)
    return worst


def smsgg_reservation(root):
    """What a HUD with sheet elements reserves on SMS / Game Gear, for the room
    generator: {"oam": sprites from the table's top, "tile_lo": the lowest
    sprite tile it (and the glyph band) occupies}, or None when it reserves
    nothing - so every other world's rooms.mos is byte-identical."""
    panels = load_hud_panels(os.path.join(root, "scripts"))
    plan = _sheet_plan(panels, {p.get("sheet"): load_sheet(root, p.get("sheet"))
                                for p in panels if p.get("sheet")})
    if not plan["oam"]:
        return None
    n_glyphs = 0
    st = tuple(sprite_text_consoles(root))
    if set(SHEET_CONSOLES) & set(st):
        try:
            variables = dict(compile_path(os.path.join(root, "scripts")).variables)
        except (VmError, KeyError, ValueError, TypeError, IndexError):
            variables = {}
        # the SAME list emit_hud_mos uploads (one band for all of `st`)
        n_glyphs = len(_panel_glyphs([_without_sheet(p) for p in panels], variables,
                                     consoles=st))
    return {"oam": plan["oam"],
            "tile_lo": _GLYPH_TILE_TOP["sms"] - n_glyphs - plan["tiles"]}


def _without_sheet(p):
    return dict(p, element=[e for e in p.get("element", []) if not _is_sheet_element(p, e)])


def _sheet_plan(panels, sheets):
    """Validate the sheet elements and lay out their OAM slots: {"slots":
    {(pi, eid): first slot}, "oam": slots reserved (the busiest panel), "tiles":
    the biggest panel sheet's tile count}. Raises VmError on a bad reference."""
    slots, oam, tiles = {}, 0, 0
    for pi, p in enumerate(panels):
        els = [e for e in p.get("element", []) if _is_sheet_element(p, e)]
        if not els:
            continue
        sheet = (sheets or {}).get(p.get("sheet"))
        if not sheet:
            raise VmError("HUD panel %r: its sheet %r is not a sprite sheet in assets/"
                          % (p.get("name"), p.get("sheet")))
        tiles = max(tiles, sheet["tiles"])
        top = SHEET_OAM_TOP
        for e in els:
            cs = _element_consoles(p, e)
            bad = [c for c in (cs or ["every console"]) if c not in SHEET_CONSOLES]
            if bad:
                raise VmError(
                    "HUD panel %r element #%s draws from the sheet %r, which the "
                    "SMS / Game Gear can do (their sprite area has the room), not %s: "
                    "list only sms / gamegear in its Consoles"
                    % (p.get("name"), e.get("id"), p.get("sheet"), ", ".join(bad)))
            series = _cell_series(sheet, str(e.get("sprite")))
            need = 10 if e.get("kind") == "var" else (2 if e.get("kind") == "gauge" else 1)
            if len(series) < need:
                raise VmError(
                    "HUD panel %r element #%s: the sheet %r has %s cell(s) from %r; %s"
                    % (p.get("name"), e.get("id"), p.get("sheet"), len(series),
                       e.get("sprite"),
                       {"var": "a number needs <prefix>0 .. <prefix>9",
                        "gauge": "a bar needs <prefix>0 .. <prefix>N (N >= 1)",
                        "icon": "an icon needs that cell"}[e.get("kind")]))
            n = _sheet_slots_of(e)
            top -= n
            slots[(pi, e["id"])] = top + 1
        oam = max(oam, SHEET_OAM_TOP - top)
    return {"slots": slots, "oam": oam, "tiles": tiles}


def _guard_tail(L, start, guard):
    """Wrap the lines emitted since `start` (one element's statements inside a
    function body) in `if guard { ... }`; nothing when the element has no
    Consoles list, so a HUD without one is emitted unchanged."""
    if guard and len(L) > start:
        L[start:] = (["        if %s {" % guard] + ["    " + ln for ln in L[start:]]
                     + ["        }"])


def _sheet_pos(p, e):
    """(px, py) expressions of a sheet element: its pixels, else its cell."""
    rows = int(p.get("rows", 1))
    px = str(int(e["px"])) if e.get("px") is not None else str(int(e.get("x", 0)) * 8)
    py = (str(int(e["py"])) if e.get("py") is not None
          else "(SCREEN_ROWS - %d + %d) * 8" % (rows, int(e.get("y", 0))))
    return px, py


def _emit_sheet_module(L, panels, plan, sheets, variables, n_glyphs):
    """The module-level half of the panel-sheet elements: the tile base, each
    cell series as a table, a cached last value per live element and one draw
    function each, all under the SMS/GG guard (they reference the base, which
    exists only there). Returns {(pi, eid): (draw fn or None, guard)}."""
    guard = _console_guard(SHEET_CONSOLES)
    base = _GLYPH_TILE_TOP["sms"] - n_glyphs - plan["tiles"]
    out, tables, body = {}, {}, []
    for pi, p in enumerate(panels):
        sheet = sheets.get(p.get("sheet"))
        for e in p.get("element", []):
            if not _is_sheet_element(p, e):
                continue
            eg = _console_guard(_element_consoles(p, e)) or guard
            series = _cell_series(sheet, str(e["sprite"]))
            key = (p.get("sheet"), str(e["sprite"]))
            if key not in tables:
                tables[key] = "HUD_C%d" % len(tables)
                body.append("        const %s: array[u8, %d] = [%s]"
                            % (tables[key], len(series), ", ".join(str(s) for s in series)))
            tab = tables[key]
            slot = plan["slots"][(pi, e["id"])]
            px, py = _sheet_pos(p, e)
            idx = variables.get(e.get("var"))
            if e["kind"] == "icon" or idx is None:
                out[(pi, e["id"])] = (None, eg)
                continue
            fn = "draw_p%d_e%d" % (pi, e["id"])
            L.append("    var last_p%d_e%d: i16 = -1" % (pi, e["id"]))
            f = ["        local function %s() {" % fn,
                 "            var raw: i16 = core.var_get(%d)   -- %s" % (idx, e["var"]),
                 "            var v: u8 = raw"]
            w = _sheet_slots_of(e)
            if e["kind"] == "var":
                # right-aligned digits, one sprite a cell, leading cells '0'
                # when the field pads with zeros, else parked
                f += ["            var nd: u8 = 1",
                      "            if v >= 10 { nd = 2 }",
                      "            if v >= 100 { nd = 3 }",
                      "            var off: u8 = 0",
                      "            if nd < %d { off = %d - nd }" % (w, w)]
                for j in range(w):
                    p10 = 10 ** (w - 1 - j)
                    digit = "v % 10" if p10 == 1 else "v / %d %% 10" % p10
                    f += ["            if off > %d {" % j]
                    if e.get("pad") == "0":
                        f += ["                sprite.set_tile(%d, HUD_SHEET_BASE + %s[0])" % (slot + j, tab),
                              "                sprite.move(%d, %s + %d, %s)" % (slot + j, px, j * 8, py)]
                    else:
                        f += ["                sprite.move(%d, 0, SCREEN_HEIGHT)" % (slot + j)]
                    f += ["            } else {",
                          "                var d%d: u8 = %s" % (j, digit),
                          "                sprite.set_tile(%d, HUD_SHEET_BASE + %s[d%d])" % (slot + j, tab, j),
                          "                sprite.move(%d, %s + %d, %s)" % (slot + j, px, j * 8, py),
                          "            }"]
            else:
                # a bar of `w` sprites, each showing 0..S-1 units of the fill
                s1 = len(series) - 1
                maxidx = variables.get(e.get("max_var"))
                f.append("            var fill: u16 = v")
                if maxidx is not None:
                    f += ["            var mv: u16 = core.var_get(%d)   -- %s (max)" % (maxidx, e["max_var"]),
                          "            if mv == 0 { mv = 1 }",
                          "            fill = fill * %d / mv" % (w * s1)]
                else:
                    f.append("            fill = fill * %d / %d" % (w * s1, max(1, int(e.get("max", 0) or 1))))
                for j in range(w):
                    f += ["            var k%d: u16 = 0" % j,
                          "            if fill > %d { k%d = fill - %d }" % (j * s1, j, j * s1),
                          "            if k%d > %d { k%d = %d }" % (j, s1, j, s1),
                          "            var c%d: u8 = k%d" % (j, j),
                          "            sprite.set_tile(%d, HUD_SHEET_BASE + %s[c%d])" % (slot + j, tab, j),
                          "            sprite.move(%d, %s + %d, %s)" % (slot + j, px, j * 8, py)]
            f += ["            last_p%d_e%d = raw" % (pi, e["id"]),
                  "        }"]
            body += f
            out[(pi, e["id"])] = (fn, eg)
    L += ["",
          "    -- The panels' OWN sprite art (a panel `sheet`), SMS / Game Gear only: its",
          "    -- tiles sit just below the glyph band at the top of the 256-tile sprite",
          "    -- area, its sprites take the table's last slots, and the room load keeps",
          "    -- the actors below both (rooms.mos HUD_OAM / OBJ_TILE_TOP).",
          "    if %s {" % guard,
          "        const HUD_SHEET_BASE: u8 = %d" % base]
    L += body
    L += ["    }"]
    return out


def emit_hud_mos(panels, variables, script_names=(), sprite_text=(), reshow=False,
                 sheets=None):
    """The ``hud`` module text for ``panels`` (the hud.toml list) resolving var
    names through ``variables`` (name -> heap index). Renders (GB window band):
    ``label`` (fixed text), ``var`` (right-aligned pad-filled number), ``tiles``
    (a raw w*h ``plot_tile`` stamp) and ``gauge`` (a hearts/bar tile STRIP driven by
    a var). ``icon`` (a pinned sprite) is authored but not emitted here yet.

    ``sprite_text`` = the consoles that must draw the band's TEXT with sprites
    instead (``sprite_text_consoles`` - a window-less tilemap console whose
    background scrolls). Those arms are `if platform` forks, so every other
    target keeps the identical text path, and an empty tuple emits the identical
    module it always did.

    Elements can ATTACH EVENT scripts (``script_names`` = the project's scripts):
    an ``on_show`` runs once when the panel is shown, an ``on_change`` runs when a
    dynamic field's value changes (edge-triggered via ``core.spawn`` -> a fresh
    thread; a full thread pool no-ops gracefully). On_change fires on the tilemap
    main-loop diff path (GB); the Lynx full-redraw path does not diff, so on_change
    is a GB-family feature for now."""
    script_names = set(script_names or ())
    # The panel-sheet elements are laid out apart (OAM slots from the table's
    # top, their own draw path); everything below sees the panels WITHOUT them,
    # so a HUD that has none emits exactly what it always did.
    sheets = sheets or {}
    plan = _sheet_plan(panels, sheets)
    full_panels = panels
    panels = [_without_sheet(p) for p in panels]
    any_dyn = any(_dyn_fields(p, variables) for p in full_panels)
    any_slot = any(_slot(e, k, script_names)
                   for p in full_panels for e in p.get("element", [])
                   for k in ("on_show", "on_change"))
    # The sprite-text backend, resolved per console by the caller. `glyphs` is
    # the uploaded character list; `spr_guard` the `if platform` condition every one of
    # its statements sits under (so nothing below changes for any other target).
    sprite_text = tuple(c for c in _SPRITE_TEXT_CONSOLES if c in (sprite_text or ()))
    glyphs = _panel_glyphs(panels, variables, consoles=sprite_text) if sprite_text else []
    if not glyphs:
        sprite_text = ()
    spr_guard = " or ".join('platform == "%s"' % c for c in sprite_text)
    gindex = {c: i for i, c in enumerate(glyphs)}
    # reserve OAM slots (top-down) for the sprite-drawn elements: an icon takes 1
    # slot; a gauge WITH a `sprite_tile` takes `max` slots (its hearts, drawn as
    # sprites on the tilemap-less Lynx / PCE). base = the element's first slot.
    sprite_base, top = {}, HUD_ICON_TOP
    for pi, p in enumerate(panels):
        for e in p.get("element", []):
            if e.get("kind") == "icon":
                sprite_base[(pi, e["id"])] = top
                top -= 1
            elif (e.get("kind") == "gauge" and e.get("sprite_tile")
                  and e.get("max") and e.get("var") in variables):
                top -= int(e["max"])
                sprite_base[(pi, e["id"])] = top + 1
    any_sprite = bool(sprite_base)
    reserved_slots = list(range(top + 1, HUD_ICON_TOP + 1))   # every reserved slot
    # ... and, BELOW those, one slot per drawn character of the sprite-text
    # backend (a label's non-space characters, a var field's whole width). They
    # are parked/positioned only inside `spr_guard`, so no other console pays
    # for the reservation.
    text_base, ttop = {}, top
    for pi, p in enumerate(panels):
        for e in p.get("element", []):
            n = 0
            if e.get("kind") == "label" and e.get("text"):
                n = len([c for c in str(e["text"]) if c != " "])
            elif e.get("kind") == "var" and e.get("var") in variables:
                n = int(e.get("width", 0)) or 3
            if sprite_text and n:
                ttop -= n
                text_base[(pi, e["id"])] = ttop + 1
    text_slots = list(range(ttop + 1, top + 1))
    L = ["-- GENERATED by MosaiK8 Studio (mosaik_vm) -- the overlay HUD (the HUD editor).",
         "-- show(id) plots a panel onto the GB window band (labels + tiles + gauges +",
         "-- number fields); update() refreshes a dynamic field only when its heap cell",
         "-- changed (no redraw script needed) + runs an attached On Change script.",
         "-- Regenerated from scripts/hud.toml.",
         "",
         'module "hud" {']
    if any_dyn or any_slot or reshow:
        L.append('    import "vm.core"')
    L.append('    import "graphics.text"')
    if any_sprite or sprite_text or plan["oam"]:
        L.append('    import "graphics.sprite"')
    if plan["oam"]:
        L.append('    import "graphics.palette"   -- the sheet uploads in its own colours')
    if any_slot:
        L.append('    import "scripts"')
    if any(p.get("sprite_cut") for p in panels):
        L.append('    import "vm.player"     -- the shmup ship stays above a cut band')
    L.append("")
    L.append("    var active: u8 = 255        -- the shown panel index (255 = none)")
    if sprite_text:
        # The band's glyph tiles. A window-less console whose background SCROLLS
        # (see _SPRITE_TEXT_CONSOLES) cannot hold a plotted band at a screen
        # position, so the band's characters are drawn as SPRITES out of these.
        L += ["",
              "    -- The HUD band's own glyph tiles, uploaded from the console font at",
              "    -- run time. %s has a persistent tilemap and NO window layer,"
              % "/".join(sprite_text),
              "    -- so a plotted band is a MAP cell: over a room bigger than the screen it",
              "    -- would slide away with the level. Re-plotting it in screen space cannot",
              "    -- fix that (a cell has no sub-tile position, so the band would sawtooth by",
              "    -- up to 7 px), and neither can a raster split (the SMS VDP latches its",
              "    -- vertical scroll once per frame). So the text is drawn with SPRITES,",
              "    -- which live in screen space. Uploaded by show(), positioned per redraw.",
              "    -- Every other console keeps the plotted text path, unchanged."]
        L.append("    -- The base tile is counted DOWN from the top of each console's sprite")
        L.append("    -- tile table, so the game's own sheet (which uploads from 0) is never")
        L.append("    -- touched. SMS/GG sprite patterns are a separate 256-tile VDP area (the")
        L.append("    -- emote's four sit at its top); the PCE's is the cc65 backend's 40-entry")
        L.append("    -- RAM table, so a big sheet there crowds the glyphs out rather than")
        L.append("    -- overwriting art (the engine drops an out-of-range tile upload).")
        for gi, group_top in enumerate(sorted({_GLYPH_TILE_TOP[c] for c in sprite_text},
                                              reverse=True)):
            cs = [c for c in sprite_text if _GLYPH_TILE_TOP[c] == group_top]
            kw = "    if" if gi == 0 else "    } else if"
            L += ["%s %s {" % (kw, " or ".join('platform == "%s"' % c for c in cs)),
                  "        const HUD_GLYPH_BASE: u8 = %d" % (group_top - len(glyphs))]
        L.append("    }")
        L += ["    -- The CHARACTERS the band draws, as codes: show() uploads each one's",
              "    -- glyph from the console font the toolchain links (sprite.font_glyph),",
              "    -- so no font's pixels are carried here.",
              "    if %s {" % spr_guard,
              "        const HUD_CHARS: array[u8, %d] = [" % len(glyphs)]
        for i, c in enumerate(glyphs):
            L.append("            %d%s   -- '%s'"
                     % (ord(c), "," if i + 1 < len(glyphs) else "", c))
        L += ["        ]",
              "    }"]

    sheet_fns = {}
    if plan["oam"]:
        sheet_fns = _emit_sheet_module(
            L, full_panels, plan, sheets, variables,
            len(glyphs) if set(sprite_text) & set(SHEET_CONSOLES) else 0)
    sheet_guard = _console_guard(SHEET_CONSOLES)
    sheet_slots = list(range(SHEET_OAM_TOP - plan["oam"] + 1, SHEET_OAM_TOP + 1))

    # module-scope cached last-drawn value per dynamic field (redraw only on change)
    for pi, p in enumerate(panels):
        for e in _dyn_fields(p, variables):
            L.append("    var last_p%d_e%d: i16 = -1" % (pi, e["id"]))
            # a gauge with a live max (HP that grows, an MP bar): track the max cell
            # too so the gauge redraws when the MAX changes, not only the value (F5).
            if e.get("kind") == "gauge" and variables.get(e.get("max_var")) is not None:
                L.append("    var last_p%d_e%d_m: i16 = -1" % (pi, e["id"]))
    L.append("")

    # per-field draw helpers + per-panel show/update
    for pi, p in enumerate(panels):
        rows = int(p.get("rows", 1))
        origin = "SCREEN_ROWS - %d" % rows          # bottom-anchored band
        name = p.get("name", "panel%d" % pi)
        dyn = _dyn_fields(p, variables)

        # a draw helper per DYNAMIC field (redrawn on change):
        for e in dyn:
            idx = variables[e["var"]]
            x = int(e.get("x", 0))
            row = "%s + %d" % (origin, int(e.get("y", 0)))
            if e["kind"] == "var":
                # pad-fill the field's fixed width, then print the value RIGHT-ALIGNED
                # (the reference-engine junk-tile fix: a shrinking number leaves no stale
                # digits). pad="0" -> leading zeros, default -> right-aligned spaces.
                width = int(e.get("width", 0)) or 3
                fill = ("0" if e.get("pad") == "0" else " ") * width
                L += ["    local function draw_p%d_e%d() {" % (pi, e["id"]),
                      "        var raw: i16 = core.var_get(%d)   -- %s" % (idx, e["var"]),
                      "        var v: u8 = raw"]
                tb = (pi, e["id"]) in text_base
                if tb:
                    # the SPRITE arm: the same right-aligned field, one sprite per
                    # cell out of the uploaded digit glyphs (see HUD_CHARS).
                    L.append("        if %s {" % spr_guard)
                    L += _sprite_number(text_base[(pi, e["id"])], gindex, width,
                                        e.get("pad") == "0", x, row, "        ")
                    L.append("        } else {")
                ind = "    " if tb else ""
                L += [ind + '        text.print_string(%d, %s, "%s")' % (x, row, _escape(fill)),
                      ind + "        var nd: u8 = 1",
                      ind + "        if v >= 10 { nd = 2 }",
                      ind + "        if v >= 100 { nd = 3 }",
                      ind + "        var off: u8 = 0",
                      ind + "        if nd < %d { off = %d - nd }" % (width, width),
                      ind + "        text.print_number(%d + off, %s, v)" % (x, row)]
                if tb:
                    L.append("        }")
                L += ["        last_p%d_e%d = raw" % (pi, e["id"]),
                      "    }"]
            else:
                # gauge: a strip of `max` cells. On a tilemap console it is a TILE
                # strip on the window band -- cell i FULL (base_tile) when the value
                # exceeds i, else EMPTY (base_tile + 1). On the tilemap-less Lynx /
                # PCE (where plot_tile no-ops) a gauge with a `sprite_tile` draws as
                # SPRITE hearts: `v` of `max` sprites shown, the rest parked. The two
                # code paths are forked with MODULE-LEVEL conditional compilation
                # (`if platform` folds at parse, so only the target's path is emitted)
                # -- an `if platform` inside the function body would not fold.
                mx = int(e["max"])
                base = int(e.get("base_tile", 0))
                fid = "draw_p%d_e%d" % (pi, e["id"])
                # `max_var` (optional): a proportional fill - `max` fixed cells filled
                # to value/max_var (a health BAR). Without it, discrete (cell i full
                # iff value > i). max_var resolves to a heap cell like the value.
                maxidx = variables.get(e.get("max_var"))
                cond = "gi < fill" if maxidx is not None else "v > gi"
                tile_body = [
                    "        for gi in 0..%d {" % mx,
                    "            var gt: u8 = %d" % (base + 1),
                    "            if %s { gt = %d }" % (cond, base),
                    "            text.plot_tile(%d + gi, %s, gt)" % (x, row),
                    "        }"]

                def _gauge_fn(body, ind=""):
                    pre = [ind + "    local function %s() {" % fid,
                           ind + "        var v: u8 = core.var_get(%d)   -- %s (gauge)"
                           % (idx, e["var"])]
                    if maxidx is not None:              # proportional: fill = v*max/max_var
                        pre += [ind + "        var mv: u16 = core.var_get(%d)   -- %s (max)"
                                % (maxidx, e["max_var"]),
                                ind + "        if mv == 0 { mv = 1 }",
                                ind + "        var vv: u16 = v",
                                ind + "        var fill: u8 = vv * %d / mv" % mx]
                    tail = [ind + "        last_p%d_e%d = core.var_get(%d)"
                            % (pi, e["id"], idx)]
                    if maxidx is not None:          # cache the max too (F5 max_var redraw)
                        tail.append(ind + "        last_p%d_e%d_m = core.var_get(%d)"
                                    % (pi, e["id"], maxidx))
                    return pre + [ind + ln for ln in body] + tail + [ind + "    }"]

                if (pi, e["id"]) in sprite_base:                 # has a Lynx/PCE sprite path
                    gb = sprite_base[(pi, e["id"])]
                    spr = int(e["sprite_tile"])
                    rpx = "(%s + %d) * 8" % (origin, int(e.get("y", 0)))
                    sprite_body = [
                        "        for gi in 0..%d {" % mx,
                        "            if %s {" % cond,
                        "                sprite.set_tile(%d + gi, %d)" % (gb, spr),
                        "                sprite.move(%d + gi, %d + gi * 8, %s)" % (gb, x * 8, rpx),
                        "            } else {",
                        "                sprite.move(%d + gi, 0, SCREEN_HEIGHT)" % gb,
                        "            }",
                        "        }"]
                    # The Lynx and PCE ALWAYS take the sprite arm (plot_tile
                    # no-ops there, so the tile strip would not draw at all);
                    # SMS/GG join them when this world scrolls, where the tile
                    # strip draws but then slides away with the level.
                    gauge_spr = ['platform == "lynx"', 'platform == "pce"']
                    gauge_spr += ['platform == "%s"' % c for c in sprite_text
                                  if c in ("sms", "gamegear")]
                    L.append("    if %s {" % " or ".join(gauge_spr))
                    L += _gauge_fn(sprite_body, "    ")
                    L.append("    } else {")
                    L += _gauge_fn(tile_body, "    ")
                    L.append("    }")
                else:
                    L += _gauge_fn(tile_body)

        # draw<pi>: a FULL redraw -- plot every STATIC element (label, tiles) then
        # every dynamic field. Used by show<pi> (initial) and the Lynx present hook.
        L.append("    local function draw%d() {   -- %s" % (pi, name))
        for e in p.get("element", []):
            k = e.get("kind")
            start, eguard = len(L), _console_guard(_element_consoles(p, e))
            if k == "label" and e.get("text"):
                x = int(e.get("x", 0))
                row = "%s + %d" % (origin, int(e.get("y", 0)))
                if (pi, e["id"]) in text_base:
                    L.append("        if %s {" % spr_guard)
                    L += _sprite_label(text_base[(pi, e["id"])], gindex,
                                       str(e["text"]), x, row, "        ")
                    L.append("        } else {")
                    L.append('            text.print_string(%d, %s, "%s")'
                             % (x, row, _escape(str(e["text"]))))
                    L.append("        }")
                else:
                    L.append('        text.print_string(%d, %s, "%s")'
                             % (x, row, _escape(str(e["text"]))))
            elif k == "tiles":
                # a raw w*h stamp of consecutive tile ids (row-major from base_tile),
                # plotted through the text-layer router onto the window map.
                x = int(e.get("x", 0))
                row = "%s + %d" % (origin, int(e.get("y", 0)))
                w = int(e.get("w", 1)) or 1
                h = int(e.get("h", 1)) or 1
                base = int(e.get("base_tile", 0))
                eid = e["id"]
                L += ["        for tj%d in 0..%d {" % (eid, h),
                      "            for ti%d in 0..%d {" % (eid, w),
                      "                text.plot_tile(%d + ti%d, %s + tj%d, %d + tj%d * %d + ti%d)"
                      % (x, eid, row, eid, base, eid, w, eid),
                      "            }",
                      "        }"]
            _guard_tail(L, start, eguard)
        for e in dyn:
            start = len(L)
            L.append("        draw_p%d_e%d()" % (pi, e["id"]))
            _guard_tail(L, start, _console_guard(_element_consoles(p, e)))
        for e in full_panels[pi].get("element", []):
            fn, g = sheet_fns.get((pi, e.get("id")), (None, None))
            if fn:
                L += ["        if %s {" % g, "            %s()" % fn, "        }"]
        L.append("    }")

        # show<pi>: route to the window band (no-op off the GB family), draw, then
        # run each element's On Show script once (core.spawn -> a fresh thread).
        L += ["    local function show%d() {" % pi]
        fp = full_panels[pi]
        if any(_is_sheet_element(fp, e) for e in fp.get("element", [])):
            # the panel's sheet, in its SMS/GG sprite palette slot (the 2bpp
            # nibble map rooms.mos uses for a kind's colours), then the default
            s = int(fp.get("sheet_slot", 0) or 0) & 3
            nib = ((4 * s + 3) << 12) | ((4 * s + 2) << 8) | ((4 * s + 1) << 4)
            stem = str(fp["sheet"])
            L += ["        if %s {" % sheet_guard,
                  "            palette.set_2bpp(0x%04X)" % nib,
                  "            sprite.set_data(HUD_SHEET_BASE, %s_tile_count, %s_tiles)" % (stem, stem),
                  "            palette.set_2bpp(0x3210)",
                  "        }"]
        L += ["        text.to_window(%s, %d)" % (origin, rows),
              "        draw%d()" % pi,
              # ...and only THEN show the layer. to_window prepares the band
              # and blanks it; revealing before draw() displays that blank
              # band for as long as the draw takes, which on the GB family is
              # more than one LCD frame. Same split as vm.core's box.
              "        text.win_reveal()"]
        if p.get("sprite_cut"):
            # SPRITES STOP AT THE BAND (`[[panel]] sprite_cut`): on the GB
            # family OBJ draws above the window, so an actor low on the screen
            # covered the status line. The LCD cut the text box uses
            # (text.win_sprite_cut, latched at the window's first line) - armed
            # after the reveal, so it reads the band's WY; vm.core gives a
            # box's cut back BEFORE it re-shows the band, so the band re-arms
            # its own. A no-op on a console without the window layer.
            L.append("        text.win_sprite_cut(1)   -- no sprites over the band")
            # ...and a band nothing is drawn over is not PLAYFIELD: a vertical
            # shmup's ship stops above it (vm.player; VM_SHMUP_INSET).
            L.append("        player.set_shmup_inset(%d)   -- the ship stays above it"
                     % (rows * 8))
        # pinned OAM sprites (icons): position once -- the sprite engine re-blits
        # them each frame, and the actor render never touches these high slots.
        for e in _icons(p):
            slot = sprite_base[(pi, e["id"])]
            start = len(L)
            L += ["        sprite.set_tile(%d, %d)" % (slot, int(e.get("base_tile", 0))),
                  "        sprite.move(%d, %d, (%s + %d) * 8)   -- icon"
                  % (slot, int(e.get("x", 0)) * 8, origin, int(e.get("y", 0)))]
            _guard_tail(L, start, _console_guard(_element_consoles(p, e)))
        sheet = sheets.get(fp.get("sheet"))
        for e in fp.get("element", []):
            fn, g = sheet_fns.get((pi, e.get("id")), (None, None))
            if g and fn is None and e.get("kind") == "icon":
                px, py = _sheet_pos(fp, e)
                cell = sheet["cells"][str(e["sprite"])][0]
                slot = plan["slots"][(pi, e["id"])]
                L += ["        if %s {" % g,
                      "            sprite.set_tile(%d, HUD_SHEET_BASE + %d)   -- %s" % (slot, cell, e["sprite"]),
                      "            sprite.move(%d, %s, %s)" % (slot, px, py),
                      "        }"]
        for e in fp.get("element", []):
            s = _slot(e, "on_show", script_names)
            if s:
                L.append("        core.spawn(scripts.ENTRY_%s)   -- On Show" % s)
        L.append("    }")

        # update<pi>: redraw only changed dynamic fields (tilemap main-loop path);
        # a change also runs the field's On Change script (edge-triggered spawn).
        L.append("    local function update%d() {" % pi)
        for e in dyn:
            start = len(L)
            idx = variables[e["var"]]
            maxidx = (variables.get(e.get("max_var"))
                      if e.get("kind") == "gauge" else None)
            if maxidx is not None:              # redraw on a value OR a max change (F5)
                cond = ("core.var_get(%d) != last_p%d_e%d or "
                        "core.var_get(%d) != last_p%d_e%d_m"
                        % (idx, pi, e["id"], maxidx, pi, e["id"]))
            else:
                cond = "core.var_get(%d) != last_p%d_e%d" % (idx, pi, e["id"])
            L.append("        if %s {" % cond)
            L.append("            draw_p%d_e%d()" % (pi, e["id"]))
            oc = _slot(e, "on_change", script_names)
            if oc:
                L.append("            core.spawn(scripts.ENTRY_%s)   -- On Change" % oc)
            L.append("        }")
            _guard_tail(L, start, _console_guard(_element_consoles(p, e)))
        for e in full_panels[pi].get("element", []):
            fn, g = sheet_fns.get((pi, e.get("id")), (None, None))
            if not fn:
                continue
            idx = variables[e["var"]]
            # (a bar's max_var is read at each redraw; the VALUE drives it)
            cond = "core.var_get(%d) != last_p%d_e%d" % (idx, pi, e["id"])
            L += ["        if %s {" % g, "            if %s {" % cond, "                %s()" % fn]
            oc = _slot(e, "on_change", script_names)
            if oc:
                L.append("                core.spawn(scripts.ENTRY_%s)   -- On Change" % oc)
            L += ["            }", "        }"]
        L.append("    }")

    # public show(id) / update() / draw() / hide() over the active panel
    L += [""]
    L += ["    function show(id: u8) {", "        active = id"]
    for slot in reversed(reserved_slots):   # clear any prior panel's sprites first
        L.append("        sprite.move(%d, 0, SCREEN_HEIGHT)" % slot)
    if sheet_slots:
        L.append("        if %s {" % sheet_guard)
        L.append("            for hs in %d..%d {" % (sheet_slots[0], sheet_slots[-1] + 1))
        L.append("                sprite.move(hs, 0, SCREEN_HEIGHT)   -- the panel sheet's sprites")
        L.append("            }")
        L.append("        }")
    if sprite_text:
        # upload the band's glyph tiles + clear a prior panel's text sprites
        L.append("        if %s {" % spr_guard)
        L.append("            for gi in 0..%d {" % len(glyphs))
        L.append("                sprite.font_glyph(HUD_GLYPH_BASE + gi, HUD_CHARS[gi])")
        L.append("            }")
        for slot in reversed(text_slots):
            L.append("            sprite.move(%d, 0, SCREEN_HEIGHT)" % slot)
        L.append("        }")
    for pi in range(len(panels)):
        kw = "if" if pi == 0 else "} else if"
        L += ["        %s id == %d {" % (kw, pi), "            show%d()" % pi]
    if panels:
        L.append("        }")
    L += ["    }", ""]
    for fn, body in (("update", "update"), ("draw", "draw")):
        L.append("    function %s() {" % fn)
        for pi in range(len(panels)):
            kw = "if" if pi == 0 else "} else if"
            L += ["        %s active == %d {" % (kw, pi), "            %s%d()" % (body, pi)]
        if panels:
            L.append("        }")
        L += ["    }", ""]
    L += ["    function hide() {",
          "        text.to_bkg()"]
    if any(p.get("sprite_cut") for p in panels):
        L.append("        text.win_sprite_cut(0)   -- the band's sprite cut goes with it")
        L.append("        player.set_shmup_inset(0)")
    for slot in reversed(reserved_slots):
        L.append("        sprite.move(%d, 0, SCREEN_HEIGHT)   -- park the HUD sprites" % slot)
    if sheet_slots:
        L.append("        if %s {" % sheet_guard)
        L.append("            for hs in %d..%d {" % (sheet_slots[0], sheet_slots[-1] + 1))
        L.append("                sprite.move(hs, 0, SCREEN_HEIGHT)   -- the panel sheet's sprites")
        L.append("            }")
        L.append("        }")
    if sprite_text:
        L.append("        if %s {" % spr_guard)
        for slot in reversed(text_slots):
            L.append("            sprite.move(%d, 0, SCREEN_HEIGHT)   -- park the band's text" % slot)
        L.append("        }")
    L += ["        active = 255",
          "    }",
          ""]
    if reshow:
        # vm.core's re-show hook (`core.set_hud_show(hud.reshow)`): raise the
        # active panel's band again after a text box / menu / room change hid
        # the window. Emitted only for a shell that registers it.
        # HUD_SHOW 0 calls this too (vm.core, after hiding the window band):
        # then it parks the panel's SPRITES, which no band hide reaches.
        park = []
        for slot in reversed(reserved_slots):
            park.append("            sprite.move(%d, 0, SCREEN_HEIGHT)" % slot)
        if sprite_text and text_slots:
            park.append("            if %s {" % spr_guard)
            park += ["                sprite.move(%d, 0, SCREEN_HEIGHT)" % s for s in reversed(text_slots)]
            park.append("            }")
        if sheet_slots:
            park += ["            if %s {" % sheet_guard,
                     "                for hs in %d..%d {" % (sheet_slots[0], sheet_slots[-1] + 1),
                     "                    sprite.move(hs, 0, SCREEN_HEIGHT)",
                     "                }",
                     "            }"]
        L += ["    function reshow() {",
              "        if core.hud_on() == 0 {"]
        L += park
        L += ["            return",
              "        }",
              "        if active != 255 {",
              "            show(active)",
              "        }",
              "    }",
              "",
              "    export show, update, draw, hide, reshow"]
    else:
        L += ["    export show, update, draw, hide"]
    L += ["}", ""]
    return "\n".join(L)


def shell_registers_reshow(root):
    """Whether the project's shell hands vm.core the HUD re-show hook
    (`core.set_hud_show(hud.reshow)`, comments stripped): then hud.mos emits
    `reshow`. Absent, the module is byte-identical to before."""
    import re
    path = os.path.join(root, "src", "main.mos")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return False
    wire = re.compile(r"core\.set_hud_show\(\s*hud\.reshow\s*\)")
    return any(wire.search(ln.split("--", 1)[0]) for ln in lines)


def generate_hud(root, out_path=None):
    """Generate ``src/hud.mos`` from ``scripts/hud.toml`` + the project's compiled
    heap map. Written by the studio scaffold + ``VmScripts.generate`` (only when a
    hud.mos already exists OR the project has a hud.toml, so hand-wired projects
    are untouched). Returns the written path, or None when the project has no HUD."""
    scripts_dir = os.path.join(root, "scripts")
    panels = load_hud_panels(scripts_dir)
    if not panels:
        return None
    try:
        variables = dict(compile_path(scripts_dir).variables)
    except (VmError, KeyError, ValueError, TypeError, IndexError):
        variables = {}
    try:
        from .loader import load_scripts
        script_names = {s.get("name") for s in load_scripts(scripts_dir) if s.get("name")}
    except (VmError, KeyError, ValueError, TypeError, IndexError):
        script_names = set()
    out_path = out_path or os.path.join(root, "src", "hud.mos")
    # Generate BEFORE opening the file - see the note in `songs.generate_songs`:
    # `open(..., "w")` truncates, so a raise while emitting left a zero-byte
    # module that the build only noticed as `imports unknown module ...`.
    sheets = {p.get("sheet"): load_sheet(root, p.get("sheet"))
              for p in panels if p.get("sheet")}
    text = emit_hud_mos(panels, variables, script_names,
                        sprite_text=sprite_text_consoles(root),
                        reshow=shell_registers_reshow(root), sheets=sheets)
    for console in SHEET_CONSOLES:
        n, line = sheet_line_load(panels, sheets, console)
        if n > SPRITES_PER_LINE:
            print("  ⚠️  HUD: %d sheet sprites on screen line %d on %s; its VDP draws %d "
                  "a line and the HUD's slots are the last, so %d of them are not drawn"
                  % (n, line, console, SPRITES_PER_LINE, n - SPRITES_PER_LINE))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return out_path
