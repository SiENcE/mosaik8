#!/usr/bin/env python3
"""The VM8 code window + the RPN jump-table invariant (review V-5, measured):

  * lib/vm/core.mos tests the 0xFF terminator BEFORE `switch tk` in rpn_eval:
    as a case it widened the value range to 0..255 for 38 labels and sdcc laid
    the switch out as a compare ladder (~150 M-cycles for a late token);
  * every fetch inside a slice takes the inline hw.peek path when a window is
    registered, and nothing outside a slice does;
  * the generated scripts.mos exports code_window() and the generated
    rooms.start() calls it; hw.peek lowers inline.

Pure Python; no toolchain needed."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik.compiler import MosaikCompiler  # noqa: E402

FAILS = []


def _exported(src, name):
    """Is `name` in ANY of a module's `export` statements?

    NOT `src.split("export")[-1]`: a module may have several export lines, and
    taking only the last one reads as "not exported" the moment a new one is
    appended below it - which is exactly what W7j and W7c each did to a
    different module, breaking a green assertion for a reason that had nothing
    to do with what it pins.
    """
    return any(name in ln for ln in src.splitlines()
               if ln.strip().startswith("export "))


def check(cond, msg):
    print(("  ok: " if cond else "  FAIL: ") + msg)
    if not cond:
        FAILS.append(msg)


def main():
    print("[VM8 code window]")
    core = open(os.path.join(ROOT, "lib", "vm", "core.mos"), encoding="utf-8").read()
    rpn = core[core.index("local function rpn_eval"):core.index("local function get_state")]
    check(rpn.index("if tk == 0xFF {") < rpn.index("switch tk {"),
          "rpn_eval tests the 0xFF terminator BEFORE the switch (keeps sdcc's jump table)")
    check("case 0xFF" not in rpn, "...and 0xFF is not a case label")
    check(core.count("hw.peek(code_base + ") == 5,
          "five inline slice fetches (f8, f16 x2, the RPN token, the opcode)")
    check("g_fetch(menu_ids + mi)" in core,
          "the menu's option ids (outside a slice) still go through g_fetch")
    check("function set_code_window(base: addr, enter: function() -> u8, leave: function(u8))" in core
          and _exported(core, "set_code_window"),
          "set_code_window is the seam and is exported")
    rc = core[core.index("local function run_context"):core.index("local function run_scripts")]
    check(rc.count("g_code_leave(saved_bank)") == 4 and rc.count("g_code_enter()") == 1,
          "run_context enters the window once and leaves it on every exit path")
    # The RESIDENT form (review V-5's last item, 2026-09-05): a blob that is
    # not in a switchable bank registers the window WITHOUT the enter/leave
    # pair, and every one of the five seam calls sits behind `code_banked`
    # (~620 T-cycles a slice on the sm83 for two calls that did nothing).
    check(rc.count("if code_banked == 1 {") == 5
          and "if code_banked == 1 { g_code_leave(saved_bank) }" in rc,
          "...and every enter/leave sits behind the code_banked flag (1 + 4)")
    check("if has_code_window == 1 { g_code_leave" not in rc,
          "...no leave is guarded by has_code_window alone")
    sw = core[core.index("function set_code_window("):core.index("function set_code_window_res(")]
    sr = core[core.index("function set_code_window_res("):core.index("function set_player(")]
    check("code_banked = 1" in sw and "code_banked = 0" in sr and "g_code_enter" not in sr,
          "set_code_window states banked, set_code_window_res states resident and takes no pair")
    check(_exported(core, "set_code_window_res"), "set_code_window_res is exported")

    # Two generated modules, from the two front ends: vm-snake's is assembled
    # from a `.v8s`, vm-showcase's compiled from event lists in a project that
    # banks its code (`[build] code_banks`) and has a generated rooms.mos.
    for proj in ("vm-snake", "vm-showcase"):
        p = os.path.join(ROOT, "projects", proj, "src", "scripts.mos")
        if not os.path.isfile(p):
            print("  skip: %s not present" % p)
            continue
        s = open(p, encoding="utf-8").read()
        check("bank(0) function code_window()" in s and "core.set_code_window(assets.address(CODE)" in s
              and re.search(r"export [^\n]*\bcode_window\b", s),
              "%s: scripts.mos defines and exports code_window()" % proj)
        check("if VM_CODE_BANKED {" in s and "core.set_code_window_res(assets.address(CODE))" in s,
              "%s: ...and forks on the build-stated VM_CODE_BANKED" % proj)
    rooms = os.path.join(ROOT, "projects", "vm-showcase", "src", "rooms.mos")
    if os.path.isfile(rooms):
        check("scripts.code_window()" in open(rooms, encoding="utf-8").read(),
              "the generated rooms.start() registers the window")

    src = '''module "main" {
    import "platform.video"
    import "platform.hardware"
    var b: u8 = 0
    function main() {
        b = hw.peek(0xC000)
        loop { video.wait_vblank() }
    }
    export main
}
'''
    for plat in ("gameboy", "lynx"):
        c = MosaikCompiler().compile_program([("main.mos", src)], platform=plat)
        check("(*(const uint8_t *)(49152U))" in c,
              "%s: hw.peek lowers to an inline plain read" % plat)

    # VM_CODE_BANKED is a build-stated define: the compiler's default is
    # False (a project without `[build] bank_bytecode` takes the resident
    # form) and the build tool states True only for a banked blob.
    fork = '''module "main" {
    import "platform.video"
    var b: u8 = 0
    function banked() { b = 1 }
    function resident() { b = 2 }
    function main() {
        if VM_CODE_BANKED { banked() } else { resident() }
        loop { video.wait_vblank() }
    }
    export main
}
'''
    c = MosaikCompiler().compile_program([("main.mos", fork)], platform="gameboy")
    check("    resident();" in c and "    banked();" not in c,
          "VM_CODE_BANKED defaults to False (the resident form)")
    c = MosaikCompiler().compile_program([("main.mos", fork)], platform="gameboy",
                                         defines={"VM_CODE_BANKED": True})
    check("    banked();" in c and "    resident();" not in c,
          "...and a build stating it takes the banked form")
    bt = open(os.path.join(ROOT, "mosaik8_build.py"), encoding="utf-8").read()
    i = bt.index("if self.config.get_bank_bytecode():")
    check("defines['VM_CODE_BANKED'] = True" in bt[i:i + 400],
          "mosaik8_build states VM_CODE_BANKED iff [build] bank_bytecode")
    if FAILS:
        print("\ncode window checks FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("\nAll code window checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
