#!/usr/bin/env python3
"""Golden (snapshot) tests for the generated C.

Compiles a few representative samples and compares the emitted C byte-for-byte
against checked-in snapshots under tests/golden/. This guards refactors that
must not change output (e.g. splitting the compiler into a package, moving the
backend preludes) -- a stray reordering of the prelude or sprite engine fails
here even though substring-based tests would pass.

Run `python tests/golden_test.py --update` to regenerate the snapshots after an
intentional codegen change (review the diff before committing).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from mosaik import MosaikCompiler

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "samples")

# (sample file, platform) pairs chosen to cover both preludes, both stdlib
# lowering maps, GBDK text+sprites, and the cc65 Suzy sprite engine.
CASES = [
    ("hello.mos", "gameboy"),   # GBDK prelude, text
    ("hello.mos", "lynx"),      # cc65 TGI prelude, text
    ("pong.mos", "gameboy"),    # GBDK sprites + input + text
    ("bounce.mos", "lynx"),     # cc65 Suzy sprite engine
]


def generate(sample, platform):
    with open(os.path.join(SAMPLES, sample), encoding="utf-8") as f:
        source = f.read()
    out = MosaikCompiler().compile(source, platform=platform)
    assert not out.startswith("Compilation error:"), \
        f"{sample} [{platform}] failed to compile:\n{out[:600]}"
    return out


def golden_path(sample, platform):
    stem = os.path.splitext(sample)[0]
    return os.path.join(GOLDEN_DIR, f"{stem}.{platform}.c")


def update():
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    for sample, platform in CASES:
        out = generate(sample, platform)
        with open(golden_path(sample, platform), "w", encoding="utf-8",
                  newline="\n") as f:
            f.write(out)
        print(f"  wrote {os.path.basename(golden_path(sample, platform))} "
              f"({len(out)} bytes)")
    print("Golden snapshots updated.")


def main():
    if "--update" in sys.argv:
        update()
        return 0

    print("Golden codegen snapshots")
    print("=" * 50)
    failures = 0
    for sample, platform in CASES:
        path = golden_path(sample, platform)
        if not os.path.exists(path):
            print(f"[FAIL] {sample} [{platform}]: no snapshot "
                  f"(run with --update)")
            failures += 1
            continue
        with open(path, encoding="utf-8") as f:
            expected = f.read()
        actual = generate(sample, platform)
        if actual == expected:
            print(f"[PASS] {sample} [{platform}]")
        else:
            failures += 1
            print(f"[FAIL] {sample} [{platform}]: generated C differs from "
                  f"{os.path.basename(path)}")
            # Show the first differing line for a quick diagnosis.
            for i, (a, b) in enumerate(zip(actual.splitlines(),
                                           expected.splitlines())):
                if a != b:
                    print(f"       first diff at line {i + 1}:")
                    print(f"         expected: {b!r}")
                    print(f"         actual:   {a!r}")
                    break
            else:
                print(f"       length differs: {len(actual)} vs {len(expected)}")

    print("=" * 50)
    if failures:
        print(f"{failures} golden check(s) FAILED "
              f"(if intentional: python tests/golden_test.py --update)")
        return 1
    print("All golden snapshots match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
