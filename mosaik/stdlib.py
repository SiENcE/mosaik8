"""Standard-library module registry.

The standard library is built into the compiler: each call's per-backend
lowering lives in the codegen STDLIB_CALLS_* maps (mosaik/codegen/), and its
public signature is registered with the TypeChecker. This set is just the
module *names* `import` recognises as stdlib (so `import "platform.video"`
resolves, while an unknown import is an error). Keep it in sync with the
STDLIB_CALLS_* maps and the spec's §6 reference.
"""

STDLIB_MODULE_NAMES = frozenset({
    'platform.video', 'platform.input', 'platform.hardware', 'platform.system',
    'platform.sound',
    'graphics.sprite', 'graphics.bkg', 'graphics.window', 'graphics.text',
    'graphics.draw', 'graphics.palette',
    # Native-extension namespaces (the escape hatch): console-specific features
    # reached as `<console>.*`. They lower to real hardware on their console and
    # to a no-op everywhere else, so one source still builds on all nine.
    'native.lynx',
})


def stdlib_module_names() -> frozenset:
    """The set of module names `import` resolves to the standard library."""
    return STDLIB_MODULE_NAMES
