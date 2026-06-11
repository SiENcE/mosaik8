#!/usr/bin/env python3
"""Cross-file module linking + stdlib-on-disk tests.

Covers MosaikCompiler.compile_program (whole-program compilation of several
sources into one C translation unit), the multi-module C name mangling and
export enforcement in CodeGenerator, module tree-shaking, the builder's
single-file import closure, and the built-in stdlib module registry.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from mosaik import MosaikCompiler, stdlib_module_names


MAIN_SRC = '''
module "main" {
    import "platform.video"
    import "graphics.text"
    import "counter"

    function main() {
        video.enable_lcd()
        loop {
            counter.tick()
            if counter.rolled_over() {
                text.print_number(2, 4, counter.seconds)
            }
            video.wait_vblank()
        }
    }

    export main
}
'''

COUNTER_SRC = '''
module "counter" {
    const TICKS_PER_SECOND: u8 = 60

    var ticks: u8 = 0
    var seconds: u8 = 0
    var wrapped: bool = false

    function tick() {
        ticks += 1
        wrapped = false
        if ticks >= TICKS_PER_SECOND {
            ticks = 0
            seconds += 1
            wrapped = true
        }
    }

    function rolled_over() -> bool {
        return wrapped
    }

    export tick, rolled_over, seconds
}
'''


def compile_two(platform='gameboy'):
    compiler = MosaikCompiler()
    return compiler.compile_program(
        [('main.mos', MAIN_SRC), ('counter.mos', COUNTER_SRC)],
        platform=platform)


def test_stdlib_module_registry():
    """`import` resolves the built-in stdlib module names."""
    expected = {'platform.video', 'platform.input', 'platform.hardware',
                'platform.system', 'graphics.sprite', 'graphics.bkg',
                'graphics.window', 'graphics.text', 'graphics.draw'}
    assert expected <= stdlib_module_names()
    # A program importing a stdlib module compiles; an unknown one errors.
    ok = MosaikCompiler().compile_program(
        [('m', 'module "main" { import "platform.video"\n'
               'function main() { video.enable_lcd() } export main }')],
        platform='gameboy')
    assert not ok.startswith("Compilation error:"), ok[:300]
    print("PASS stdlib module registry")


def test_cross_module_link():
    """Two sources compile into one TU with mangled, export-checked names."""
    for platform in ('gameboy', 'lynx'):
        out = compile_two(platform)
        assert not out.startswith("Compilation error:"), out[:800]
        # Module-level symbols are mangled per module; main stays main.
        assert 'void main(void)' in out
        assert 'void counter_tick(void)' in out
        assert 'uint8_t counter_seconds = 0;' in out
        assert '#define counter_TICKS_PER_SECOND (60)' in out
        # Cross-module references resolve to the mangled names.
        assert 'counter_tick();' in out
        assert 'counter_rolled_over()' in out
        assert ', counter_seconds);' in out
        # Intra-module references are mangled too.
        assert 'counter_ticks += 1;' in out
    print("PASS cross-module linking (gameboy + lynx)")


def test_single_module_unchanged():
    """A single-module program keeps plain C names (no mangling)."""
    out = MosaikCompiler().compile(COUNTER_SRC, platform='gameboy')
    assert not out.startswith("Compilation error:"), out[:800]
    assert 'void tick(void)' in out
    assert 'uint8_t seconds = 0;' in out
    assert 'counter_tick' not in out
    print("PASS single-module output unchanged")


def expect_error(sources, needle, label):
    out = MosaikCompiler().compile_program(sources, platform='gameboy')
    assert out.startswith("Compilation error:"), f"{label}: compiled but should not"
    assert needle in out, f"{label}: wrong message: {out.splitlines()[0]}"
    print(f"PASS {label}")


def test_diagnostics():
    # Calling a function the module does not export.
    expect_error(
        [('m', 'module "main" { import "counter"\n'
               'function main() { counter.hidden() } export main }'),
         ('c', 'module "counter" { function hidden() {} '
               'function tick() {} export tick }')],
        'not exported', 'unexported symbol is an error')
    # Importing a module nothing in the build defines.
    expect_error(
        [('m', 'module "main" { import "nosuch"\n'
               'function main() {} export main }')],
        'unknown module', 'unknown import is an error')
    # The same module name in two files.
    expect_error(
        [('a.mos', 'module "x" { }'), ('b.mos', 'module "x" { }')],
        'duplicate module', 'duplicate module is an error')
    # Using a module without importing it.
    expect_error(
        [('m', 'module "main" { function main() { counter.tick() } '
               'export main }'),
         ('c', 'module "counter" { function tick() {} export tick }')],
        'does not import', 'missing import is an error')
    # Two entry points.
    expect_error(
        [('a', 'module "a" { function main() {} export main }'),
         ('b', 'module "b" { function main() {} export main }')],
        'entry point', 'two main() definitions is an error')
    # A user module shadowing a stdlib alias.
    expect_error(
        [('m', 'module "main" { function main() {} export main }'),
         ('v', 'module "video" { }')],
        'reserved for the standard library', 'stdlib alias collision is an error')


def test_shadowing():
    """Params/locals shadow module globals and must not be mangled."""
    src_a = '''
    module "main" {
        import "util"
        var counter: u8 = 0
        function main() {
            var ticks: u8 = util.scale(counter)
            counter = ticks
        }
        export main
    }
    '''
    src_b = ('module "util" { var ticks: u8 = 9\n'
             'function scale(v: u8) -> u8 { return v }\n export scale }')
    out = MosaikCompiler().compile_program([('a', src_a), ('b', src_b)],
                                           platform='gameboy')
    assert not out.startswith("Compilation error:"), out[:800]
    assert 'uint8_t ticks = util_scale(main_counter);' in out
    print("PASS locals shadow module symbols")


def test_tree_shaking():
    """Modules unreachable from main() are pruned; libraries keep everything."""
    compiler = MosaikCompiler()
    main = ('module "main" { import "used"\n'
            'function main() { used.go() } export main }')
    used = 'module "used" { function go() {} export go }'
    orphan = 'module "orphan" { function noop() {} export noop }'
    out = compiler.compile_program([('m', main), ('u', used), ('o', orphan)],
                                   platform='gameboy')
    assert not out.startswith("Compilation error:"), out[:400]
    assert 'used_go' in out, "reachable module was dropped"
    assert 'orphan' not in out and 'noop' not in out, "orphan not pruned"

    # No main() -> a library build keeps every module (no single root).
    lib = compiler.compile_program(
        [('a', 'module "a" { function helper() {} export helper }'),
         ('b', 'module "b" { function other() {} export other }')],
        platform='gameboy')
    assert 'a_helper' in lib and 'b_other' in lib, "library build pruned a module"
    print("PASS module tree-shaking")


def test_builder_import_closure():
    """Single-file mode pulls in sibling .mos files for non-stdlib imports."""
    from mosaik8 import MosaikBuilder
    with tempfile.TemporaryDirectory() as tmp:
        entry = os.path.join(tmp, 'game.mos')
        with open(entry, 'w', encoding='utf-8') as f:
            f.write('module "game" {\n    import "helpers.math"\n'
                    '    function main() { }\n    export main\n}\n')
        helper_dir = os.path.join(tmp, 'helpers')
        os.makedirs(helper_dir)
        helper = os.path.join(helper_dir, 'math.mos')
        with open(helper, 'w', encoding='utf-8') as f:
            f.write('module "helpers.math" {\n'
                    '    function double(v: u8) -> u8 { return v + v }\n'
                    '    export double\n}\n')

        builder = MosaikBuilder()
        closure = builder._resolve_import_closure(entry)
        assert closure is not None
        assert [os.path.basename(p) for p in closure] == ['game.mos', 'math.mos']

        # A missing import reports an error instead of compiling.
        with open(entry, 'w', encoding='utf-8') as f:
            f.write('module "game" {\n    import "nothere"\n'
                    '    function main() { }\n    export main\n}\n')
        assert builder._resolve_import_closure(entry) is None
    print("PASS builder import closure")


def main():
    print("Cross-file module linking tests")
    print("=" * 50)
    test_stdlib_module_registry()
    test_cross_module_link()
    test_single_module_unchanged()
    test_diagnostics()
    test_shadowing()
    test_tree_shaking()
    test_builder_import_closure()
    print("All multifile tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
