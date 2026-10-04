#!/usr/bin/env python3
"""The player's kind-level table reads are LATCHED (vm.canim, 2026-09-05).

`draw_player` used to ask the banked clips module for the kind's metasprite
size (`g_mw`/`g_mh`) and the frame's sparse mask (`g_msk`) on every step
frame. The size is a function of the KIND alone and `set_player` is p_clip's
one writer, so it is read there once (`p_mw`/`p_mh`); the mask is a function
of (kind, state, facing, frame) and is cached per frame index for the
(state, facing) the R1 shadows hold (`p_mskc`/`p_mskv`). The asymmetry of
every such cache: a stale HIT draws the wrong frame silently, so the drop
sites are the contract. Source-level; no toolchain needed."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANIM = os.path.join(ROOT, "lib", "vm", "canim.mos")
FAILS = []


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label, ("  -- %s" % note) if note else ""))
    if not cond:
        FAILS.append(label)


def body(src, name):
    m = re.search(r"^(\s*)(?:hot )?(?:local )?function %s\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    indent = m.group(1)
    end = re.compile(r"^%s\}" % indent, re.M).search(src, m.end())
    return src[m.start():end.end() if end else len(src)]


def main():
    src = open(CANIM, encoding="utf-8").read()
    sp = body(src, "set_player")
    dp = body(src, "draw_player")
    fold = 'if platform == "lynx" or platform == "pce" {'

    # --- the size latch ------------------------------------------------------
    check("set_player latches the kind's metasprite size",
          "p_mw = meta_w_of(kind)" in sp and "p_mh = meta_h_of(kind)" in sp)
    check("...only for a real kind (255 = no player art)",
          re.search(r"if kind != 255 \{\s*\n\s*p_mw = meta_w_of\(kind\)", sp) is not None)
    check("p_mw/p_mh have ONE writer (set_player)",
          src.count("p_mw = ") == 1 and src.count("p_mh = ") == 1)
    sel = dp.split("if VM_CLIP_SEL {", 1)[1].split("if VM_CLIP_SEL {} else {", 1)[0]
    off = dp.split("if VM_CLIP_SEL {} else {", 1)[1]
    check("the batched arm draws through the latch",
          "draw_meta(p_base, t, p_mw, p_mh, msk)" in sel)
    check("...behind the Lynx/PCE fold, whose arm keeps the table reads",
          re.search(r"%s\s*\n\s*draw_meta\(p_base, t, meta_w_of\(p_clip\), meta_h_of\(p_clip\), msk\)\s*\n\s*\} else \{\s*\n\s*draw_meta\(p_base, t, p_mw, p_mh, msk\)"
                    % re.escape(fold), sel) is not None)
    check("the OFF arm is untouched (reads the tables)",
          "p_mw" not in off and "p_mskc" not in off
          and "meta_w_of(p_clip)" in off)

    # --- the mask cache ------------------------------------------------------
    check("set_player drops the whole mask cache",
          "p_mskv = 0" in sp)
    check("a state or facing change drops it (the R1 shadows are the key)",
          re.search(r"if p_dst != p_astate or p_dfa != p_face \{\s*\n\s*p_mskv = 0", sel) is not None)
    check("a miss reads the table and fills the entry",
          re.search(r"msk = g_msk\(p_clip, p_astate, p_face, p_frame\)\s*\n\s*p_mskc\[p_frame\] = msk\s*\n\s*p_mskv \|= mb", sel) is not None)
    check("a hit reads the entry", "msk = p_mskc[p_frame]" in sel)
    check("frames past the cache read the table as before",
          re.search(r"\} else \{\s*\n\s*msk = g_msk\(p_clip, p_astate, p_face, p_frame\)\s*\n\s*\}\s*\n\s*\}\s*\n\s*\}", sel) is not None)
    check("the cache is behind the Lynx/PCE fold (their arm reads the table)",
          re.search(r"if g_sparse == 1 \{\s*\n\s*%s\s*\n\s*msk = g_msk\(p_clip, p_astate, p_face, p_frame\)\s*\n\s*\} else \{"
                    % re.escape(fold), sel) is not None)
    check("the cache state is declared inside the fold (OFF is byte-identical)",
          re.search(r"%s\s*\n\s*\} else \{\s*\n\s*var p_mw: u8\s*\n\s*var p_mh: u8\s*\n\s*var p_mskc: array\[u16, 16\]\s*\n\s*var p_mskv: u16"
                    % re.escape(fold), src) is not None)
    check("p_mskv is written only by the two drop sites and the fill",
          src.count("p_mskv = 0") == 2 and src.count("p_mskv |= mb") == 1
          and len(re.findall(r"p_mskv\s*=[^=]", src)) == 2)

    if FAILS:
        print("\nFAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("\ncanim_player_cache_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
