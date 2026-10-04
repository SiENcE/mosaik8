#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""First-class function pointers (callbacks).

A `function(T...) -> R` type lowers to a synthesized C function-pointer typedef;
a bare/qualified function name used as a value is the pointer (no `&` operator);
calling through one (`cb(x)`, `table[i](x)`) just works. Portability rule: a
callback may only target a HOME-bank function -- referencing a `bank(N)` function
is a hard compile error on consoles where banking is real (the sdcc banked
far-call is call-site codegen, not a dispatchable address), and a no-op concern
on consoles where bank() is ignored. Emitted only when used, so callback-free
programs stay byte-identical (no `gbs_fnptr` typedef block).
"""

from mosaik import MosaikCompiler


def compile_for(src, platform='gameboy'):
    return MosaikCompiler().compile(src.strip(), platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


# A program exercising every callback shape: typed var initialized to a
# function, an uninitialized array of callbacks, a function-typed parameter,
# a struct field of function type, and calls through each.
SRC = '''
module "cb" {
    import "graphics.sprite"

    var hit: u8 = 0

    function apply0(slot: u8, frame: u8) { sprite.set_tile(slot, frame) }
    function apply1(slot: u8, frame: u8) { sprite.set_tile(slot, (frame + 1)) }

    type Anim = struct {
        period: u8,
        cb: function(u8, u8)
    }

    var direct: function(u8, u8) = apply0
    var table: array[function(u8, u8), 2]
    var rec: Anim

    function run(f: function(u8, u8), s: u8) {
        f(s, 0)
    }

    function main() {
        table[0] = apply0
        table[1] = apply1
        rec.cb = apply1
        direct(0, 1)
        table[1](1, 2)
        rec.cb(2, 3)
        run(apply0, 3)
        run(direct, 4)
    }
}
'''

# Cross-module: module "b" stores and calls module "a"'s exported function.
CROSS = '''
module "a" {
    import "graphics.sprite"
    function paint(slot: u8, frame: u8) { sprite.set_tile(slot, frame) }
    export paint
}
module "b" {
    import "a"
    var cb: function(u8, u8) = a.paint
    function main() {
        cb(0, 1)
    }
}
'''

# A callback referencing a banked function -- the portability error.
BANKED = '''
module "bk" {
    var slot: u8 = 0
    bank(2) function handler(i: u8, frame: u8) { slot = i }
    var cb: function(u8, u8) = handler
    function main() { slot = 1 }
}
'''

# A direct CALL to a banked function is fine (the trampoline path).
BANKED_DIRECT = '''
module "bk2" {
    var slot: u8 = 0
    bank(2) function handler(i: u8) { slot = i }
    function main() { handler(7) }
}
'''

# A callback-free program -- must stay byte-identical (no fnptr machinery).
PLAIN = '''
module "p" {
    import "platform.video"
    var n: u8 = 0
    function step() { n += 1 }
    function main() { video.enable_lcd() loop { step() video.wait_vblank() } }
}
'''

# engine.anim (the callback-driven animator), composed with a small game module.
# Compiled together in-process (the lib search path is mosaik8.py's job).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "lib", "engine", "anim.mos"), encoding="utf-8") as f:
    ANIM_LIB = f.read()
ANIM_GAME = '''
module "ad" {
    import "platform.video"
    import "graphics.sprite"
    import "engine.anim"
    function hero(slot: u8, frame: u8) { sprite.set_tile(slot, frame) }
    function main() {
        video.enable_lcd()
        anim.set(0, 8, 4, hero)
        loop { anim.tick() video.wait_vblank() }
    }
}
'''


def main():
    print("First-class function pointers (callbacks)")
    print("=" * 50)
    ok = True

    out = compile_for(SRC)
    built = not out.startswith("Compilation error:")
    ok &= check("callback program compiles (gameboy)", built)
    # One typedef shared by every function(u8, u8) signature.
    ok &= check("emits a single shared fnptr typedef",
                out.count("typedef void (*gbs_fnptr_0)(uint8_t, uint8_t);") == 1)
    ok &= check("typed var holds the function pointer",
                "gbs_fnptr_0 direct = apply0;" in out)
    ok &= check("array-of-callbacks declarator",
                "gbs_fnptr_0 table[2];" in out)
    ok &= check("struct field of function type",
                "gbs_fnptr_0 cb;" in out)
    ok &= check("function-typed parameter",
                "void run(gbs_fnptr_0 f, uint8_t s)" in out)
    ok &= check("call through a variable pointer", "direct(0, 1);" in out)
    ok &= check("call through an array element", "table[1](1, 2);" in out)
    ok &= check("call through a struct field", "rec.cb(2, 3);" in out)
    ok &= check("address taken in initializer is forward-declared",
                "void apply0(uint8_t slot, uint8_t frame);" in out
                and out.index("void apply0(uint8_t slot, uint8_t frame);")
                    < out.index("gbs_fnptr_0 direct = apply0;"))

    # Builds on a cc65 console too (portable C function pointers).
    for plat in ("lynx", "pce", "nes"):
        o = compile_for(SRC, plat)
        ok &= check("compiles on %s" % plat,
                    not o.startswith("Compilation error:")
                    and "gbs_fnptr_0" in o)

    # Cross-module reference.
    cm = compile_for(CROSS)
    ok &= check("cross-module function reference resolves",
                not cm.startswith("Compilation error:")
                and "gbs_fnptr_0 b_cb = a_paint;" in cm)

    # Banking guard: a reference to a banked function is rejected where banking
    # is real, but fine where bank() is ignored.
    bk_gb = compile_for(BANKED, "gameboy")
    ok &= check("gameboy: reference to bank(N) function is a compile error",
                "cannot reference banked function 'handler'" in bk_gb)
    bk_ly = compile_for(BANKED, "lynx")
    ok &= check("lynx: bank() ignored -> callback to it is allowed",
                "cannot reference banked" not in bk_ly
                and "gbs_fnptr_0 cb = handler;" in bk_ly)
    bd = compile_for(BANKED_DIRECT, "gameboy")
    ok &= check("gameboy: direct call to a banked function is NOT flagged",
                "cannot reference banked" not in bd)

    # Gating: a callback-free program emits none of the fnptr machinery.
    for plat in ("gameboy", "lynx", "pce", "nes"):
        o = compile_for(PLAIN, plat)
        ok &= check("%s: callback-free program emits no fnptr typedef" % plat,
                    "gbs_fnptr" not in o
                    and "address is taken" not in o)

    # engine.anim composes the feature (animator fires apply callbacks).
    an = MosaikCompiler().compile_program(
        [("anim.mos", ANIM_LIB.strip()), ("ad.mos", ANIM_GAME.strip())],
        platform="gameboy")
    ok &= check("engine.anim drives apply callbacks through a pointer array",
                not an.startswith("Compilation error:")
                and "engine_anim_a_apply[i](i, engine_anim_a_frame[i]);" in an
                and "gbs_fnptr_0 engine_anim_a_apply[8];" in an)

    print()
    if ok:
        print("All callback checks passed")
        return 0
    print("callback checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
