#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""Per-platform conditional compilation (`if platform == "..."`).

Verifies that the condition is evaluated against the build target so only the
matching branch's declarations are kept, that `else if` chains and aliases
work, and that an unresolved condition falls back to the `then` branch.
"""

from mosaik import MosaikCompiler

SRC = '''
module "main" {
    if platform == "gameboy_color" {
        function tag() -> u8 { return 1 }
    } else if platform == "nes" {
        function tag() -> u8 { return 2 }
    } else if platform == "sms" or platform == "gamegear" {
        function tag() -> u8 { return 3 }
    } else {
        function tag() -> u8 { return 0 }
    }
    export tag
}
'''


def tag_for(platform):
    c = MosaikCompiler().compile(SRC.strip(), platform=platform)
    if c.startswith("Compilation error:"):
        raise AssertionError(f"{platform}: {c.splitlines()[0]}")
    # The selected branch is the only tag() emitted.
    import re
    m = re.search(r"return (\d+)", c)
    return int(m.group(1)) if m else None


def check(platform, expected):
    got = tag_for(platform)
    ok = got == expected
    print(f"  [{'PASS' if ok else 'FAIL'}] {platform:<16} -> tag {got} (want {expected})")
    return ok


def main():
    print("Conditional compilation (per-platform selection)")
    print("=" * 50)
    cases = [
        ("gameboy", 0),         # else branch
        ("gameboy_color", 1),   # then branch
        ("gbc", 1),             # alias of gameboy_color
        ("nes", 2),             # else if
        ("sms", 3),             # else if with or
        ("gamegear", 3),        # other side of the or
        ("megaduck", 0),        # unmatched -> else branch
    ]
    ok = all(check(p, e) for p, e in cases)

    # An unresolvable condition keeps the `then` branch (legacy behaviour).
    legacy = '''
    module "m" {
        var enabled: bool = true
        if enabled { function f() -> u8 { return 7 } } else { function f() -> u8 { return 9 } }
        export f
    }
    '''
    c = MosaikCompiler().compile(legacy.strip(), platform="gameboy")
    legacy_ok = "return 7" in c and "return 9" not in c
    print(f"  [{'PASS' if legacy_ok else 'FAIL'}] unresolved condition keeps then-branch")

    # A nested `if` inside a branch is itself conditional compilation, so a
    # 3-way split written as a braced `else { if ... }` must parse and select
    # the same branch as the `else if` chain (not a parse error).
    nested = '''
    module "m" {
        if platform == "gameboy" {
            function tag() -> u8 { return 1 }
        } else {
            if platform == "sms" {
                function tag() -> u8 { return 2 }
            } else {
                function tag() -> u8 { return 3 }
            }
        }
        export tag
    }
    '''
    import re
    nested_ok = True
    for plat, want in [("gameboy", 1), ("sms", 2), ("lynx", 3)]:
        cc = MosaikCompiler().compile(nested.strip(), platform=plat)
        m = re.search(r"return (\d+)", cc)
        got = int(m.group(1)) if m else None
        good = got == want and not cc.startswith("Compilation error:")
        nested_ok = nested_ok and good
        print(f"  [{'PASS' if good else 'FAIL'}] braced else {{ if }}  {plat:<10} -> tag {got} (want {want})")

    defines_ok = test_defines()

    if ok and legacy_ok and nested_ok and defines_ok:
        print("\nAll conditional-compilation checks passed")
        return 0
    print("\nConditional-compilation checks FAILED")
    return 1


def test_defines():
    """Build-supplied compile-time flags (`defines`), and folding a conditional
    at STATEMENT level.

    The flags resolve in a conditional condition exactly like `platform`, and a
    resolved condition inside a function body drops the dead branch entirely
    instead of emitting a runtime test -- that is what lets `lib/vm/core.mos`
    guard each opcode arm on "does this game's bytecode contain it". A name the
    build did NOT pass stays unresolvable, so an ordinary runtime `if` is
    untouched and a build with no defines is byte-identical.
    """
    print("\n[defines: build-supplied compile-time flags + statement-level folding]")
    src = '''
    module "main" {
        function pick() -> u8 {
            if FEAT_A {
                return 1
            } else {
                return 2
            }
        }
        export pick
    }
    '''
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    def gen(defines):
        return MosaikCompiler().compile_program(
            [("t.mos", src.strip())], platform="gameboy", defines=defines)

    on = gen({"FEAT_A": True})
    off = gen({"FEAT_A": False})
    none = gen(None)

    check("return 1" in on and "return 2" not in on,
          "a true flag keeps only the `then` branch")
    check("return 2" in off and "return 1" not in off,
          "a false flag keeps only the `else` branch")
    # The dead branch is GONE, not merely `if (0)`-guarded: no test survives.
    check("if" not in off.split("pick")[-1].split("}")[0] or "FEAT_A" not in off,
          "the folded branch leaves no runtime test / no undefined symbol")
    # No defines -> unresolvable -> the ordinary runtime `if` is preserved,
    # which is the byte-identical default every existing program relies on.
    check("FEAT_A" in none, "with no defines the condition stays a runtime if")

    # A flag combines with `not` / `and` / `or` and with `platform`, since they
    # share one evaluator.
    combo = '''
    module "main" {
        function pick() -> u8 {
            if not FEAT_A and platform == "gameboy" {
                return 7
            }
            return 8
        }
        export pick
    }
    '''
    c = MosaikCompiler().compile_program([("t.mos", combo.strip())],
                                         platform="gameboy",
                                         defines={"FEAT_A": False})
    check("return 7" in c, "`not FLAG and platform == ...` folds true")
    c2 = MosaikCompiler().compile_program([("t.mos", combo.strip())],
                                          platform="gameboy",
                                          defines={"FEAT_A": True})
    check("return 7" not in c2, "the same condition folds false when the flag flips")

    # A module-level conditional resolves on a flag too (same evaluator).
    mod = '''
    module "main" {
        if FEAT_B {
            function tag() -> u8 { return 5 }
        } else {
            function tag() -> u8 { return 6 }
        }
        export tag
    }
    '''
    m = MosaikCompiler().compile_program([("t.mos", mod.strip())],
                                         platform="gameboy",
                                         defines={"FEAT_B": False})
    check("return 6" in m and "return 5" not in m,
          "a flag resolves a MODULE-level conditional as well")
    return ok


if __name__ == "__main__":
    sys.exit(main())
