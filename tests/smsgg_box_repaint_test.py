#!/usr/bin/env python3
"""A box close on SMS / Game Gear keeps script-written background tiles.

On these consoles a dialogue box or menu is plotted INTO the scene tilemap,
and its close repaints the room (rooms.mos `redraw` / `redraw_cols` /
`redraw_rows`). For a room small enough to paint that was `scenes.paint`,
which re-uploads the whole TILESET as well as the map - so every tile a
script had rewritten (`bkg_tile`, the `[[replace_tile]]` bank) went back to
its room art at the first close. vm-fjaum's battle HUD (names, level
digits, HP bars) vanished on the SMS the moment the first box closed; the
Game Boy keeps its box on the window and never repaints.

Under glyph-buffer text (`studio.toml [scenes] glyph_text`) a box never
touches tile DATA, so the close now takes `scenes.repaint`: `paint` with the
SMS/GG tileset upload skipped (`ts_keep`). Both generators decide it on the
same two facts (a replacement bank AND glyph_text), so the call and the
definition cannot disagree; any other world is unchanged.

This checks, on copies of vm-fjaum:
  * both facts: the scenes module defines `repaint` inside the SMS/GG fork,
    paint()'s SMS/GG upload sits under `if ts_keep == 0`, and all three
    redraws call `scenes.repaint(room)`;
  * glyph_text off, or no replacement bank: no `ts_keep`, no `repaint`, the
    redraws call `scenes.paint(room)` as before;
  * the warm() path (a streamed room) is untouched in every arm.
The ROM side (the HUD's ink survives the close, GB and SMS) is
`projects/vm-fjaum/verify.py`, run by `project_verify_test`.

    python tests/smsgg_box_repaint_test.py
"""
import io
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import regen_generated  # noqa: E402

PROJECT = os.path.join(ROOT, "projects", "vm-fjaum")
FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)[:400]) if detail else ""))


def generate(tmp, arm, glyph=True, repl=True):
    """(scenes.mos text, rooms.mos text) of a copy with the two facts set."""
    root = os.path.join(tmp, arm)
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns("build"))
    sp = os.path.join(root, "studio.toml")
    s = io.open(sp, encoding="utf-8").read()
    assert re.search(r"(?m)^glyph_text = true$", s), "vm-fjaum sets glyph_text"
    if not glyph:
        s = re.sub(r"(?m)^glyph_text = true$", "glyph_text = false", s)
        io.open(sp, "w", encoding="utf-8", newline="\n").write(s)
    if not repl:
        wp = os.path.join(root, "world.toml")
        w = io.open(wp, encoding="utf-8").read()
        n = len(re.findall(r"(?m)^\[\[replace_tile\]\]$", w))
        assert n, "vm-fjaum has a replacement bank"
        # drop every [[replace_tile]] table (each runs to the next header)
        w = re.sub(r"(?ms)^\[\[replace_tile\]\]\n.*?(?=^\[|\Z)", "", w)
        io.open(wp, "w", encoding="utf-8", newline="\n").write(w)
    out = {}
    for mod in ("scenes", "rooms"):
        path = os.path.join(root, "src", mod + ".mos")
        out[mod] = regen_generated._new_text(root, path, mod)
    return out["scenes"], out["rooms"]


def fn(src, name):
    """The body of `function name(` up to the next function."""
    m = re.search(r"function %s\(.*?(?=\n    (?:local )?function |\n    export )" % name,
                  src, re.S)
    return m.group(0) if m else ""


def redraws(rooms):
    return [fn(rooms, n) for n in ("redraw", "redraw_cols", "redraw_rows")]


def main():
    print("=== smsgg_box_repaint_test ===")
    tmp = tempfile.mkdtemp(prefix="repaint_")
    try:
        scenes, rooms = generate(tmp, "both")
        print("[a replacement bank AND glyph_text]")
        rp = fn(scenes, "repaint")
        check("scenes defines repaint(): ts_keep = 1, paint, ts_keep = 0",
              re.search(r"ts_keep = 1\s+paint\(scene\)\s+ts_keep = 0", rp) is not None, rp)
        fork = scenes[:scenes.index("function repaint(")]
        check("...inside the SMS / Game Gear fork, with its flag",
              fork.rstrip().endswith("var ts_keep: u8 = 0")
              and 'if platform == "sms" or platform == "gamegear" {' in fork[-400:],
              fork[-400:])
        check("...and exported", re.search(r"export .*\brepaint\b", scenes) is not None)
        paint = fn(scenes, "paint")
        check("paint()'s SMS/GG tileset upload is skipped while ts_keep is set",
              re.search(r"if ts_keep == 0 \{\s+ts_upload\(scene\)\s+\}", paint) is not None,
              paint)
        warm = fn(scenes, "warm")
        check("warm() (a streamed room) uploads as before", warm and "ts_keep" not in warm
              and "ts_upload(scene)" in warm, warm)
        rd = redraws(rooms)
        check("all three box-close redraws call scenes.repaint(room)",
              all("scenes.repaint(room)" in r and "scenes.paint(room)" not in r for r in rd),
              rd)
        check("...and no other caller does (a room LOAD still paints)",
              rooms.count("scenes.repaint(") == 3 and "scenes.paint(" in rooms)

        for arm, kw, what in (("noglyph", {"glyph": False}, "glyph_text off"),
                              ("norepl", {"repl": False}, "no replacement bank")):
            print("[%s: as before]" % what)
            s2, r2 = generate(tmp, arm, **kw)
            check("no ts_keep, no repaint() in the scenes module",
                  "ts_keep" not in s2 and "function repaint(" not in s2)
            check("the redraws call scenes.paint(room)",
                  all("scenes.paint(room)" in r and "scenes.repaint" not in r for r in redraws(r2)),
                  redraws(r2))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%s" % ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
