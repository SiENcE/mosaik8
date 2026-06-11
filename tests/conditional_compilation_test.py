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

from mosaik_compiler import MosaikCompiler

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

    if ok and legacy_ok:
        print("\nAll conditional-compilation checks passed")
        return 0
    print("\nConditional-compilation checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
